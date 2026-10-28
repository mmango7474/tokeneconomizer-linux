"""Bounded, repo-scoped warm start from pre-install Claude/Codex transcripts.

Discovery uses the optional Rust scanner, with an equivalent stdlib fallback.
Old logs supply micro-skills only: today's catalog cannot prove past tool exposure.
No log is executed. Imports use stable snapshots and the normal fold deduplication.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time

from . import fold
from .paths import home_dir, repo_identity

HEADER_BYTES = 262144
MAX_FILES = 10000
MAX_SESSION_BYTES = 64 * 1024 * 1024
QUIET_S = 600
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")


def history_root(ctx):
    home = home_dir(ctx.env)
    if ctx.host.name == "claude":
        return os.path.join(ctx.env.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude"),
                            "projects")
    if ctx.host.name == "codex":
        return os.path.join(ctx.env.get("CODEX_HOME") or os.path.join(home, ".codex"), "sessions")
    return None


def _open_regular(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("history input must be a regular file")
    return os.fdopen(fd, "rb")


def _header(path, host):
    with _open_regular(path) as fh:
        data = fh.read(HEADER_BYTES)
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict):
            continue
        if host == "codex":
            if row.get("type") != "session_meta":
                continue
            row = row.get("payload")
            if not isinstance(row, dict):
                continue
            sid = row.get("id")
        else:
            if row.get("isSidechain") is True:
                return None
            sid = row.get("sessionId")
        cwd = row.get("cwd")
        if (isinstance(cwd, str) and os.path.isabs(cwd) and "\0" not in cwd
                and isinstance(sid, str) and sid and "\0" not in sid):
            return cwd, sid
    return None


def scan_python(root, host):
    """Read at most 256 KiB per log; never follow symlink files or directories."""
    out, count = [], 0
    if os.path.islink(root):
        raise ValueError("history root must not be a symlink")
    if not os.path.isdir(root):
        return out
    def onerror(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = sorted(d for d in dirs if d != "subagents" and
                         not (host == "claude" and d.startswith("agent-")) and
                         not os.path.islink(os.path.join(base, d)))
        for name in sorted(files):
            path = os.path.join(base, name)
            if not name.endswith(".jsonl") or os.path.islink(path):
                continue
            if host == "claude" and name.startswith("agent-"):
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError(f"history exceeds {MAX_FILES} files; select a smaller --root")
            try:
                metadata = os.stat(path, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                identity = _header(path, host)
                if identity:
                    out.append(dict(path=os.path.abspath(path), cwd=identity[0],
                                    session_id=identity[1], mtime_ns=metadata.st_mtime_ns,
                                    size=metadata.st_size))
            except OSError:
                continue
    return sorted(out, key=lambda r: (r["mtime_ns"], r["path"]))


def scanner(env):
    override = env.get("TE_HISTORY_SCANNER")
    if override == "python":
        return None
    if override:
        return override
    core = Path(__file__).resolve().parents[2]
    for path in (core / "native/bin/te-history", core / "bin/te-history"):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which("te-history", path=env.get("PATH"))


def discover(root, host, env):
    native = scanner(env)
    if native:
        result = subprocess.run([native, "scan", "--root", os.path.abspath(root), "--host", host],
                                capture_output=True, text=True, encoding="utf-8", timeout=60,
                                check=True)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("invalid native scanner result")
        return rows, "rust"
    return scan_python(root, host), "python"


def warmstart(ctx, root=None, limit=100, days=30, dry_run=False, paid=False, exclude=()):
    if ctx.host.name not in ("claude", "codex"):
        raise ValueError("history import currently supports claude and codex")
    if limit < 1 or days < 1:
        raise ValueError("limit and days must be positive")
    root = os.path.abspath(root or history_root(ctx))
    rows, backend = discover(root, ctx.host.name, ctx.env)
    now = time.time()
    report = dict(backend=backend, root=root, candidates=0, imported=0, skipped=0,
                  failed=0, dry_run=dry_run, sessions=[])
    # Independent context: no persistent changes to the user's live configuration.
    historical = copy.copy(ctx)
    historical.config = copy.deepcopy(ctx.config)
    historical.config.toolprune = "off"
    historical.config.repomap = "off"
    historical.config.microskills = "envfacts+distiller" if paid else "envfacts"
    historical.config.shadow = False
    known = ctx.store.read("folded.json") or {}
    selected, roots = [], {}
    seen = set(exclude) | set(known)
    for row in rows:
        sid, cwd = row.get("session_id"), row.get("cwd")
        age = now - row["mtime_ns"] / 1e9
        if (not isinstance(sid, str) or not SAFE_ID.fullmatch(sid) or sid in seen
                or not isinstance(cwd, str) or not os.path.isabs(cwd)
                or age < QUIET_S or age > days * 86400 or row["size"] > MAX_SESSION_BYTES):
            report["skipped"] += 1
            continue
        # Compare checkout roots, not remote identity: different checkouts can differ.
        if cwd not in roots:
            roots[cwd] = os.path.realpath(repo_identity(cwd, ctx.env).root)
        if roots[cwd] != os.path.realpath(ctx.repo.root):
            report["skipped"] += 1
            continue
        seen.add(sid)
        selected.append(row)
    report["candidates"] = len(selected)
    # Oldest first, including on later invocations when a backlog exceeds the batch limit.
    for row in selected[:limit]:
        sid, path = row["session_id"], row["path"]
        report["sessions"].append(sid)
        if dry_run:
            continue
        try:
            # Revalidate identity/size/age before copying; do not import a growing log.
            before = os.stat(path, follow_symlinks=False)
            if (os.path.islink(path) or before.st_size != row["size"]
                    or before.st_mtime_ns != row["mtime_ns"]):
                report["skipped"] += 1
                continue
            with tempfile.TemporaryDirectory(prefix="te-history-") as tmp:
                snapshot = os.path.join(tmp, "transcript.jsonl")
                with _open_regular(path) as src, open(snapshot, "wb") as dst:
                    remaining = MAX_SESSION_BYTES + 1
                    while remaining:
                        block = src.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        dst.write(block)
                        remaining -= len(block)
                after = os.stat(path, follow_symlinks=False)
                if (before.st_ino != after.st_ino or before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns
                        or os.path.getsize(snapshot) != row["size"]
                        or _header(snapshot, ctx.host.name) != (row["cwd"], sid)):
                    report["skipped"] += 1
                    continue
                record = fold.fold_session(historical, sid, transcript_path=snapshot,
                                           source_transcript=path, historical=True,
                                           render_profile=(ctx.config.microskills
                                               if ctx.config.microskills != "off" else None))
                if record and not record.get("error"):
                    report["imported"] += 1
                elif record:
                    report["failed"] += 1
        except Exception:
            # One malformed historical log must not starve all later sessions.
            # Normal hook dispatch also isolates parser failures. Preserve diagnostics.
            from .hook import _record_error
            _record_error(ctx.env)
            report["failed"] += 1
    if not dry_run:
        ctx.store.write("warmstart.json", dict(report, checked_at=time.time()))
    return report


def spawn_warmstart(ctx, session_id):
    """At most one background import per hour; never scan history in the hook."""
    root = history_root(ctx)
    if (not ctx.config.warmstart or ctx.config.shadow or ctx.config.microskills == "off"
            or not root or not os.path.isdir(root)):
        return
    with ctx.store.locked("warmstart-schedule", timeout=0) as got:
        if not got:
            return
        prior = ctx.store.read("warmstart-schedule.json") or {}
        if time.time() - prior.get("at", 0) < 3600:
            return
        ctx.spawn(["warmstart", "--exclude", session_id], "warmstart.log")
        ctx.store.write("warmstart-schedule.json", {"at": time.time()})
