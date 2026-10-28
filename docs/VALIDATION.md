# Validation, 2026-10-28

Validated the exported source on Linux x86-64 under WSL2 (Ubuntu, Python 3.12), including
building and exercising the standalone marketplace. Testing the extracted source also
verifies that source packaging restores the Git symlink that this Windows checkout stores
as a plain text placeholder. The working Windows checkout itself is not the Linux release.

- Python: **805 passed, 6 skipped**, `python -m pytest tests experiments/tests`.
- Rust: **6 integration tests passed** on `x86_64-unknown-linux-musl`.
- `cargo fmt --check` and `cargo clippy --locked --all-targets -- -D warnings` passed.
- 18 warm-start tests cover Claude and Codex fact learning, next-session injection,
  defaults/opt-out, read-only preview, native/Python parity, active-log exclusion,
  changed logs, symlinks, corrupted input, missing-log retries, duplicate copies,
  batch ordering, concurrent imports, preserving existing facts, protecting tool-pruning
  state, scheduling, and native discovery from an independently installed plugin bundle.
- The portable scanner was built using Rust 1.99.1; it is a 576,640-byte static PIE ELF.
  SHA-256: `ea831ecf91748af44cfedf495d438fa4a81238f37059d54fb21ffb15b429d118`.
- REA independently identified the final ELF/hash. Ghidra 12.1.4 analyzed it and decompiled
  the scanner and identity parser. [Evidence and limits](INVESTIGATION.md).

Synthetic discovery benchmark: 1,000 local logs, 5 warm-cache runs, equal output checked.
The final static musl build measured median **0.103 seconds**, against **0.154 seconds** for
the Python fallback: about **1.5× faster discovery**. An earlier dynamically linked GNU
build measured 3.8× in a separate run; that is not the packaged binary. Timings vary with
filesystem, cache state, allocator, machine load, and transcript shape. Reproduce using
`python3 scripts/benchmark_history.py` after building the scanner.

No real model calls, billing benchmark, or live host-account installation was performed.
The video's token/cost savings have not been reproduced. This fork imports micro-skills
from history; historical tool pruning and old loop-guard envelopes are deliberately not
inferred from today's catalog or configuration. Live host hooks and their retained upstream
features are exercised with offline fixtures; Linux ARM64 and other distributions were not
executed during this validation. A CI workflow is included but has not run remotely.
