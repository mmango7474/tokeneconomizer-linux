import builtins
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tokeneconomizer.plugin import fold, history, hosts
from tokeneconomizer.plugin.config import LiveConfig, load_config
from tokeneconomizer.plugin.context import LiveContext
from tokeneconomizer.trace.calls import call_cwds, load_trajectory
from tests.test_live_hooks import env, repo, trap_session, call  # noqa: F401


def log(root, cwd, sid, age=1200, host="claude"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / (sid + ".jsonl")
    if host == "claude":
        trap_session(str(path), str(cwd), 1)
        text = path.read_text()
        path.write_text(json.dumps(dict(type="user", cwd=str(cwd), sessionId=sid)) + "\n" + text)
    else:
        path.write_text(json.dumps(dict(type="session_meta", payload=dict(cwd=str(cwd), id=sid)))
                        + "\n")
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def context(env, repo):
    return LiveContext.build(hosts.ClaudeCode(), repo, dict(env, TE_HISTORY_SCANNER="python"),
                             settle=False)


def test_defaults_and_explicit_opt_out(tmp_path):
    assert LiveConfig().microskills == "envfacts+distiller"
    assert LiveConfig().warmstart
    cfg = load_config(str(tmp_path), env={"TE_MICROSKILLS": "off", "TE_WARMSTART": "off"})
    assert cfg.microskills == "off" and not cfg.warmstart


def test_background_import_is_rate_limited_and_excludes_current(tmp_path, env, repo, monkeypatch):
    logs = tmp_path / "claude" / "projects"
    logs.mkdir(parents=True)
    ctx = context(dict(env, CLAUDE_CONFIG_DIR=str(logs.parent)), repo)
    jobs = []
    monkeypatch.setattr(ctx, "spawn", lambda args, logfile: jobs.append((args, logfile)))
    monkeypatch.setattr(history, "discover", lambda *a: pytest.fail("hook must not scan inline"))
    history.spawn_warmstart(ctx, "current")
    history.spawn_warmstart(ctx, "current")
    assert jobs == [(["warmstart", "--exclude", "current"], "warmstart.log")]
    ctx.config.warmstart = False
    ctx.store.write("warmstart-schedule.json", {"at": 0})
    history.spawn_warmstart(ctx, "another")
    assert len(jobs) == 1


def test_bundle_uses_its_native_scanner_without_source_checkout(tmp_path):
    if not history.scanner({"PATH": ""}):
        pytest.skip("native scanner not built")
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "marketplace"
    subprocess.run([sys.executable, str(root / "scripts/build_plugin.py"), "--out", str(out)],
                   check=True, capture_output=True)
    logs = tmp_path / "logs"
    log(logs, str(tmp_path / "repo"), "past")
    env = dict(os.environ, TE_PYTHON=sys.executable, PYTHONPATH="",
               TOKENECONOMIZER_HOME=str(tmp_path / "state"))
    env.pop("TE_HISTORY_SCANNER", None)
    result = subprocess.run(["sh", str(out / "tokeneconomizer/scripts/te"), "warmstart",
                             "--host", "claude", "--cwd", str(tmp_path / "repo"),
                             "--root", str(logs), "--dry-run"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["backend"] == "rust" and report["sessions"] == ["past"]
    assert not (tmp_path / "state").exists()


def test_history_learns_facts_and_injects_without_pruning_or_paid_calls(tmp_path, env, repo, monkeypatch):
    root = tmp_path / "history"
    for i in range(4):
        log(root, repo, f"past-{i}", age=2000 - i)
    from tokeneconomizer.plugin import sidecall
    def forbidden(*args, **kwargs):
        pytest.fail("history must not request a paid runner by default")
    monkeypatch.setattr(sidecall, "runner_for", forbidden)
    ctx = context(env, repo)
    result = history.warmstart(ctx, str(root))
    assert result["imported"] == 4
    assert ctx.store.read("injection.json")["rendered"]
    assert ctx.store.read("untool.json") is None
    assert history.warmstart(ctx, str(root))["imported"] == 0
    assert len(ctx.store.read_lines("ledger.jsonl")) == 4
    assert all(r["source"] == "history" for r in ctx.store.read_lines("ledger.jsonl"))
    assert all(os.path.exists(r["transcript"]) for r in ctx.store.read("folded.json").values())
    out = call(env, dict(hook_event_name="SessionStart", source="startup", session_id="new",
                         cwd=repo), "claude")
    assert "pytest" in json.dumps(out)


def test_preview_is_read_only_and_scope_filters(tmp_path, env, repo):
    root = tmp_path / "history"
    log(root, repo, "eligible")
    log(root, repo, "active", age=0)
    log(root, repo, "ancient", age=40 * 86400)
    log(root, tmp_path / "other", "unrelated")
    log(root / "subagents", repo, "child")
    log(root, repo, "agent-child")
    log(root, repo, "current")
    ctx = context(env, repo)
    report = history.warmstart(ctx, str(root), dry_run=True, exclude=["current"])
    assert report["sessions"] == ["eligible"]
    assert not os.path.exists(ctx.store.base)


def test_header_skips_malformed_nested_and_truncated_metadata(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'bad\n[]\n{"tool_result":{"cwd":"/evil","sessionId":"x"}}\n'
                     b'{"cwd":"/repo","sessionId":"ok"}')
    assert history.scan_python(str(tmp_path), "claude") == []
    with path.open("ab") as fh:
        fh.write(b"\n")
    assert history.scan_python(str(tmp_path), "claude")[0]["session_id"] == "ok"


def test_scan_ignores_symlinks(tmp_path):
    source = tmp_path / "source"
    path = log(source, "/repo", "one")
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link.jsonl").symlink_to(path)
        (root / "dir").symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert history.scan_python(str(root), "claude") == []
    with pytest.raises(ValueError):
        history.scan_python(str(root / "dir"), "claude")


def test_changed_log_is_not_imported(tmp_path, env, repo, monkeypatch):
    root = tmp_path / "logs"
    path = log(root, repo, "changed")
    rows = history.scan_python(str(root), "claude")
    path.write_text(path.read_text() + "{}\n")
    monkeypatch.setattr(history, "discover", lambda *a: (rows, "test"))
    ctx = context(env, repo)
    result = history.warmstart(ctx, str(root))
    assert result["imported"] == 0 and result["skipped"] == 1
    assert ctx.store.read("folded.json") is None


def test_unreadable_fold_remains_retryable(tmp_path, env, repo):
    ctx = context(env, repo)
    record = fold.fold_session(ctx, "missing", str(tmp_path / "missing"))
    assert record["error"]
    assert ctx.store.read("folded.json") is None
    assert ctx.store.read_lines("ledger.jsonl") == []


def test_cwd_parser_reads_once_and_preserves_calls(tmp_path, monkeypatch):
    path = log(tmp_path, "/repo", "parse")
    calls = load_trajectory(str(path))
    real_open, opened = builtins.open, []
    def counted(*args, **kwargs):
        opened.append(args[0])
        return real_open(*args, **kwargs)
    monkeypatch.setattr(builtins, "open", counted)
    assert len(call_cwds(str(path))) == len(calls)
    assert opened == [str(path)]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_native_and_python_scanners_agree(tmp_path, host):
    native = history.scanner({"PATH": ""})
    if not native:
        pytest.skip("build native/bin/te-history to check native parity")
    log(tmp_path, "/répo/测试", "unicode", host=host)
    (tmp_path / "bad.jsonl").write_text("not json\n")
    expected = history.scan_python(str(tmp_path), host)
    actual, backend = history.discover(str(tmp_path), host, {"TE_HISTORY_SCANNER": native})
    assert backend == "rust" and actual == expected


def codex_trap(root, cwd, sid, age=1200):
    path = log(root, cwd, sid, age=age, host="codex")
    events = [dict(type="event_msg", payload=dict(type="task_started", turn_id=sid))]
    for i, (command, output, code) in enumerate([
        ("pytest -q tests/test_a.py", "bash: pytest: command not found", 127),
        ("python -m pytest -q tests/test_a.py", "1 passed in 0.1s", 0),
    ]):
        events.append(dict(type="event_msg", payload=dict(type="item_completed", item=dict(
            type="CommandExecution", id=f"c{i}", command=command,
            aggregated_output=output, exit_code=code, status="Completed"))))
    with path.open("a") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def test_codex_history_learns_and_is_isolated_from_claude(tmp_path, env, repo):
    root = tmp_path / "codex-history"
    for i in range(4):
        codex_trap(root, repo, f"codex-{i}", age=2000 - i)
    ctx = LiveContext.build(hosts.Codex(), repo,
                            dict(env, TE_HISTORY_SCANNER="python"), settle=False)
    report = history.warmstart(ctx, str(root))
    assert report["imported"] == 4
    assert "python -m pytest" in ctx.store.read("injection.json")["rendered"]
    assert ctx.store.read("untool.json") is None
    assert history.warmstart(ctx, str(root))["imported"] == 0
    assert len(ctx.store.read_lines("ledger.jsonl")) == 4
    assert context(env, repo).store.read("injection.json") is None


def test_history_does_not_change_existing_tool_exposure_or_settings(tmp_path, env, repo):
    root = tmp_path / "history"
    log(root, repo, "old")
    ctx = context(env, repo)
    evidence = {"sessions": 17, "prompts": 93, "used_tools": ["Read"],
                "streaks": {"Edit": 3}, "proposal": ["Unused"],
                "auto_pending": {"items": ["Unused"], "since": 123}}
    ctx.store.write("untool.json", evidence)
    from pathlib import Path
    settings = Path(repo) / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text('{"permissions":{"deny":["CustomRule"]}}\n')
    before = settings.read_bytes()
    report = history.warmstart(ctx, str(root))
    assert report["imported"] == 1
    assert ctx.store.read("untool.json") == evidence
    assert settings.read_bytes() == before


def test_duplicate_copies_and_limited_backlog_do_not_double_count(tmp_path, env, repo):
    root = tmp_path / "history"
    log(root / "a", repo, "same", age=3000)
    log(root / "copy", repo, "same", age=2900)
    log(root, repo, "second", age=2000)
    log(root, repo, "third", age=1000)
    ctx = context(env, repo)
    assert history.warmstart(ctx, str(root), limit=1)["sessions"] == ["same"]
    assert history.warmstart(ctx, str(root), limit=1)["sessions"] == ["second"]
    assert history.warmstart(ctx, str(root), limit=1)["sessions"] == ["third"]
    assert history.warmstart(ctx, str(root))["imported"] == 0
    assert [r["session_id"] for r in ctx.store.read_lines("ledger.jsonl")] == [
        "same", "second", "third"]


def _concurrent_import(env, repo, root, start, results):
    try:
        ctx = context(env, repo)
        start.wait(10)
        results.put(history.warmstart(ctx, root))
    except Exception as error:
        results.put({"exception": repr(error)})


@pytest.mark.skipif(os.name != "posix", reason="cross-process flock is a Linux guarantee")
def test_concurrent_importers_fold_each_session_once(tmp_path, env, repo):
    import multiprocessing
    root = tmp_path / "history"
    for i in range(4):
        log(root, repo, f"past-{i}", age=2000 - i)
    mp = multiprocessing.get_context("fork")
    start, results = mp.Event(), mp.Queue()
    workers = [mp.Process(target=_concurrent_import,
                          args=(env, repo, str(root), start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        start.set()
        reports = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=20)
            assert worker.exitcode == 0
        assert all("exception" not in report for report in reports), reports
        assert sum(report["imported"] for report in reports) == 4
        ctx = context(env, repo)
        assert len(ctx.store.read("folded.json")) == 4
        assert len(ctx.store.read_lines("ledger.jsonl")) == 4
        assert ctx.store.read("injection.json")["sessions"] == 4
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_one_corrupt_codex_log_does_not_block_other_history(tmp_path, env, repo):
    root = tmp_path / "history"
    bad = codex_trap(root, repo, "bad", age=3000)
    with bad.open("a") as fh:
        fh.write("[]\n")
    os.utime(bad, (time.time() - 3000, time.time() - 3000))
    codex_trap(root, repo, "good", age=2000)
    ctx = LiveContext.build(hosts.Codex(), repo,
                            dict(env, TE_HISTORY_SCANNER="python"), settle=False)
    report = history.warmstart(ctx, str(root))
    assert report["imported"] >= 1
    assert "good" in ctx.store.read("folded.json")


def test_free_import_retains_existing_paid_manual_without_model_call(tmp_path, env, repo,
                                                                   monkeypatch):
    from tokeneconomizer.microskills.distiller import Distiller, Fact
    from tokeneconomizer.plugin import sidecall
    root = tmp_path / "history"
    log(root, repo, "prior")
    ctx = LiveContext.build(hosts.ClaudeCode(), repo,
                            dict(env, TE_MICROSKILLS="on", TE_HISTORY_SCANNER="python"),
                            settle=False)
    manual = Distiller(ctx.repo.label)
    fact = Fact({"id": "validated-manual", "claim": "Run the schema generator before building."})
    fact.life.state = "ACTIVE"
    fact.life.fire_num = fact.life.fire_den = 4
    manual.facts[fact.id] = fact
    manual.save(ctx.mech_dir)
    assert fact.claim in manual.render()
    def forbidden(*args, **kwargs):
        pytest.fail("free history import must not request a paid runner")
    monkeypatch.setattr(sidecall, "runner_for", forbidden)
    report = history.warmstart(ctx, str(root))
    assert report["imported"] == 1
    assert fact.claim in ctx.store.read("injection.json")["rendered"]
    assert ctx.config.microskills == "envfacts+distiller"
    persisted = Distiller.load(ctx.repo.label, ctx.mech_dir)
    assert persisted.now == manual.now
    assert persisted.facts[fact.id].to_dict() == fact.to_dict()
