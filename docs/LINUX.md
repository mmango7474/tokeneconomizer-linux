# Linux fork

Based on Microsoft TokenEconomizer commit `3cfd3f20be20ba719ade90f278180845cbaa202f`
(Apache-2.0; original license and attribution retained). The full agent runtime remains Python;
the new history-discovery component is Rust. This preserves Claude Code, Codex, Copilot CLI,
and Pi behavior instead of replacing them with an incomplete native implementation.

## Install the ready-built package

Requirements: Linux x86-64, Python 3.10 or newer, and a supported agent installed and signed in.
The included scanner is a static musl binary; it does not require Rust or a specific glibc.
Download the release from [mmango7474/tokeneconomizer-linux](https://github.com/mmango7474/tokeneconomizer-linux/releases/latest)
and extract it in a permanent location:

```sh
mkdir -p "$HOME/.local/share/tokeneconomizer-linux"
cd "$HOME/.local/share/tokeneconomizer-linux"
base=https://github.com/mmango7474/tokeneconomizer-linux/releases/download/v0.1.0-linux.1
curl -fLO "$base/tokeneconomizer-linux-marketplace.tar.gz"
curl -fLO "$base/SHA256SUMS"
grep '  tokeneconomizer-linux-marketplace.tar.gz$' SHA256SUMS | sha256sum -c -
tar -xzf tokeneconomizer-linux-marketplace.tar.gz
cd marketplace

# Codex
codex plugin marketplace add "$PWD"
codex plugin add tokeneconomizer@tokeneconomizer

# Or Claude Code
claude plugin marketplace add "$PWD"
claude plugin install tokeneconomizer@tokeneconomizer

# Or Copilot CLI
copilot plugin marketplace add "$PWD"
copilot plugin install tokeneconomizer@tokeneconomizer
```

Review the agent's hook trust prompt and restart it. Keep the extracted directory in place.
If the original plugin is installed, remove/disable that instance before installing this one:
both use the upstream plugin identity and state layout. Do not run duplicate hooks.
No plugin registration or import of your actual history was performed while building this fork.

For Pi, install the source directory with `pi install /absolute/path/to/token-optimizer`.
Its existing live integration is retained; history import currently supports Claude and Codex.

## Build from source

On Linux, with Python 3.10+, Rust/Cargo and a C linker:

```sh
git clone https://github.com/mmango7474/tokeneconomizer-linux.git
cd tokeneconomizer-linux
sh scripts/build_linux.sh
```

For a portable Linux x86-64 static binary, matching the supplied package:

```sh
rustup target add x86_64-unknown-linux-musl
TE_RUST_TARGET=x86_64-unknown-linux-musl sh scripts/build_linux.sh
```

The marketplace is in `dist/marketplace`; the distributable is
`dist/tokeneconomizer-linux-marketplace.tar.gz`. On another Linux architecture, build locally;
the default script targets the build host. Run the Python-only plugin without building Rust
if needed: history discovery has a standard-library fallback. Force it with
`TE_HISTORY_SCANNER=python`. Set `TE_HISTORY_SCANNER=/absolute/path/to/te-history` to select
a native scanner explicitly. Installed bundles carry the scanner in `lib/bin/`.

The source exporter `python3 scripts/package_source.py` includes uncommitted changes and
restores Git symlinks that Windows may have checked out as text placeholders. It requires a
Git checkout. Its output is `dist/tokeneconomizer-linux-source.tar.gz`.

## Warm-start

From the source checkout, preview and then import existing history for one repository:

```sh
python3 -m tokeneconomizer.plugin.cli --host claude --cwd /path/to/repo warmstart --dry-run
python3 -m tokeneconomizer.plugin.cli --host claude --cwd /path/to/repo warmstart
python3 -m tokeneconomizer.plugin.cli --host codex --cwd /path/to/repo warmstart
```

In a built marketplace use `sh /path/to/marketplace/tokeneconomizer/scripts/te` in place of
`python3 -m tokeneconomizer.plugin.cli`. Commands print a JSON import report. See learned
facts with `te --host claude --cwd /path/to/repo microskills` and configuration with `config`.

At SessionStart, auto-import runs in a detached process, at most once per hour per repository
and host. Imported facts become available at a subsequent session start. Run the explicit
command before starting your agent if you want to wait for learning to finish first.

Defaults and controls:

| Setting | Behavior |
|---|---|
| `microskills=on` (default) | Free facts plus the model proposer for new live sessions |
| `microskills=envfacts` | Learn only free facts in live sessions |
| `microskills=off` | Disable live micro-skills and automatic history import |
| `warmstart=on` (default) | Background import on supported hosts |
| `warmstart=off` / `TE_WARMSTART=off` | Disable background import |
| `warmstart --paid` | Allow the model proposer during this explicit import |
| `--root /path/to/logs` | Override history directory |
| `--days 30 --limit 100` | Default age window and import batch size |
| `--exclude SESSION_ID` | Exclude a session (repeatable) |

Normal config precedence remains: defaults, global file, repository file, environment.
Existing `microskills=off` is respected; remove that explicit setting to use the new default.
Explicit `warmstart` is an action even when automatic import is disabled. The command uses
free learning unless `--paid` is supplied; it leaves the persistent configuration unchanged.

Claude history defaults to `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`; Codex to
`$CODEX_HOME/sessions` or `~/.codex/sessions`. Import matches the actual checkout root, reads
identity from metadata, rejects child agents and unsafe IDs, skips logs modified in the last
ten minutes, snapshots at most 64 MiB per session, and folds each session ID once under a lock.
The active session is excluded by the automatic hook. The quiet-time heuristic cannot prove
that another long-idle session is finished; use `--exclude` for such sessions in manual imports.

Discovery reads at most 256 KiB per log and rejects a scan exceeding 10,000 JSONL files;
choose a smaller `--root` if needed. Unsupported/missing metadata is skipped. Existing logs
are not rewritten. Corrupt imports report failure and remain retryable without blocking later
logs. Error details are local in the state root's `errors.log`.

Historical imports contribute micro-skills, not tool-pruning exposure or loop-guard envelopes.
Today's tool catalog and denylist do not establish which tools existed in an old session.
Live sessions retain upstream tool pruning and loop detection. The video creator's pruning
counts and cost reductions are not promised by this implementation.

## Validation and performance

```sh
cargo test --manifest-path native/Cargo.toml --locked
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
python3 -m pytest tests experiments/tests
python3 scripts/benchmark_history.py
```

The offline suite tests actual fact learning/injection, Codex import, concurrency/idempotence,
configuration precedence, corruption, active-log filtering, native/Python parity, and plugin
packaging. Model calls are mocked; a paid benchmark against real agents was not run.
The benchmark uses synthetic local history and checks equal scanner results. It measures
discovery latency only; it makes no claim about token savings or end-to-end agent speed.
See [the investigation](INVESTIGATION.md) for source findings and Ghidra inspection evidence.
