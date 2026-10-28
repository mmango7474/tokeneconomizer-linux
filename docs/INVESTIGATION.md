# Linux fork investigation

Research date: 2026-10-28. Baseline: Microsoft TokenEconomizer commit
[`3cfd3f20be20ba719ade90f278180845cbaa202f`](https://github.com/microsoft/TokenEconomizer/tree/3cfd3f20be20ba719ade90f278180845cbaa202f).
This document distinguishes source observations, third-party claims, and proposed changes.

## The AI Labs modification

The supplied video is [This Is Microsoft's New Plan To Fix Claude's Usage Limits](https://www.youtube.com/watch?v=1kQ_1_QL5Es).
Its own embedded `videoDetails.shortDescription`, retrieved from the public YouTube watch
page, identifies the modification: read existing Claude Code sessions before learning from
new ones, so an established project does not begin with an empty history. The relevant
chapter is [Our Fix, 09:02](https://www.youtube.com/watch?v=1kQ_1_QL5Es&t=542s).

The description reports roughly 29% fewer tokens per message in one demonstration and
roughly 21% lower cost in another. Those figures are the creator's claims, not measurements
of this fork. The description also says their modified version is available through AI Labs
Pro. We did not obtain that implementation or independently reproduce its results. This
fork implements the publicly described idea independently. The video's reference to 30-day
history retention is not a guarantee about every Claude installation or its settings.

## What upstream actually does

* **Linux already works upstream.** The package is standard-library Python 3.10+ and ships
  POSIX shell launchers. A rewrite is not necessary merely to support Linux. See the pinned
  [package metadata](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/pyproject.toml)
  and [launcher](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/plugin/scripts/te).
* **Pre-install history is not discovered.** Session start calls `pending_sessions`, which
  enumerates only the plugin's own `sessions/*.json` records. It can recover sessions whose
  end hook was missed, but does not discover sessions that predate installation. This is
  visible in the pinned [controller](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/tokeneconomizer/plugin/controller.py#L171)
  and [fold implementation](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/tokeneconomizer/plugin/fold.py#L427).
* **Micro-skills default to off.** In this baseline, `on` selects `envfacts+distiller`, which
  can call a model; `envfacts` enables deterministic local learning without those calls.
  See [configuration](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/tokeneconomizer/plugin/config.py).
* **Prompt-based pruning is already upstream.** A prior change counts ten unused prompts
  across at least two sessions, or five sessions regardless of length. This fork must not
  present that existing behavior as a new optimization. See
  [commit 508fb3c](https://github.com/microsoft/TokenEconomizer/commit/508fb3c306942781d591ddc55b1d59b8eefdd7f3).

## Implementation direction

Retain the full Python runtime and its existing host adapters, learning mechanisms, hook
contracts, and compatibility tests. Add a Rust transcript-discovery accelerator where it
can reduce scanning overhead, with a Python fallback when the Rust executable is absent.
This is a hybrid implementation, not a complete Rust rewrite. It preserves functionality
that a rushed replacement of the full runtime could silently omit.

Enable full micro-skills (`envfacts+distiller`) by default for live sessions. Historical import
uses `envfacts` by default; `--paid` enables model-assisted historical learning. Existing
explicit configuration takes precedence. These are design choices, not Microsoft's defaults.

Imports are bounded, ordered by transcript modification time, idempotent, and repo-scoped.
Modification time is an ordering heuristic, not proof of original session start order.
It must exclude the current session and recently active files, distinguish subagent logs
from independent sessions, and retain provenance so imported history remains auditable.
Unreadable input must not be silently recorded as successfully learned history.

**Historical import must not create tool-pruning evidence.** The upstream
[catalog adapter](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/tokeneconomizer/plugin/adapter.py#L110)
starts with today's probe catalog. Applying it to old transcripts would claim that newly
installed tools were loaded but unused before they existed in that environment. In
addition, `_fold_toolprune` reads today's applied denylist as the session's tool mask.
That assumption does not establish what an imported historical session actually loaded.
Keeping historical learning limited to micro-skills avoids both unsupported inferences.
Pruning continues to learn from sessions observed by the installed plugin.

This deliberately does not reproduce the video's headline historical-pruning result.
Comparable pruning would require trustworthy historical catalog and mask evidence, or a
separately specified assumption that the user explicitly accepts. No percentage savings
should be promised for this implementation without workload measurements.

## Additional implemented optimization

The pinned [`trace/calls.py`](https://github.com/microsoft/TokenEconomizer/blob/3cfd3f20be20ba719ade90f278180845cbaa202f/tokeneconomizer/trace/calls.py#L48)
contains a concrete redundant read: `call_cwds(path)` first invokes `load_trajectory(path)`,
then opens the same file again and parses Claude calls a second time to recover their
working directories. The fork's shared `_read_calls` parser now reads the file once and returns
both the existing four-field call list and aligned directories. It preserves Codex's `None`
directories, Copilot's session-directory fallback, ordering, duplicate-call treatment,
and the existing public return types. This is a local CPU/I/O optimization; it does not
itself establish lower model token cost. The trace regression suite passes, and a new test
verifies one file read per `call_cwds` invocation. Missing transcripts now remain retryable
instead of being marked successfully folded. See [validation](VALIDATION.md).

## Portability finding and remaining limits

[Upstream issue 1](https://github.com/microsoft/TokenEconomizer/issues/1) reports that
locale-dependent decoding on Windows corrupts UTF-8 hook payloads and transcripts for
non-ASCII repository paths. Explicit UTF-8 handling improves portability and also avoids
depending on a Linux process's locale. This is a separate defect from the video's fix.

Source inspection establishes the observations above. It does not prove live behavior for
every current agent version, real-world billing savings, or equivalence to AI Labs' private
modification. Rust performance claims require benchmarks, and Linux compatibility claims
for this fork require Linux tests. REA/Ghidra findings, if produced for the compiled Rust
artifact, should be recorded separately with their exact artifact identity and analysis
limitations; source inspection here does not claim binary decompilation evidence.

## Ghidra inspection of the compiled Linux helper

Ghidra 12.1.4 PUBLIC, launched directly through its installed Windows headless CLI with
JDK 21.0.12.1+1, successfully imported and analyzed this fork's Linux helper on 2026-10-28.
The preceding REA `open_binary` attempt was rejected because its Windows provider profile
permits PE targets only. This is a REA provider restriction; the direct Ghidra ELF loader
worked. No additional software or global configuration was installed for this inspection.

Initial glibc-linked development artifact inspected (subsequently replaced at this path;
its imported Ghidra project remains separate):

* Path: `native/bin/te-history`; size: 464,072 bytes.
* SHA-256: `13d0034dfc559e7150769504b20f9ee8f2358865731976af335d1a03b8165641`.
* Ghidra format: Executable and Linking Format (ELF).
* Language: `x86:LE:64:default`; selected compiler convention: `gcc`. This convention name
  is Ghidra's ABI selection, not a claim that GCC compiled the Rust source.
* Analysis image base: `0x00100000`; 668 recognized functions, including 17 symbol names
  containing `te_history`. These are Ghidra's analysis results, not a complete source inventory.

The script recovered `te_history::scan` at `0x00152d60` and `te_history::identity` at
`0x00154960`. Both decompilations completed. Their generated pseudocode lengths were 37,808
and 16,350 characters respectively; the retained report intentionally limits each to 14,000
characters and marks both truncations. The selected `scan` excerpt contains references to
`symlink_metadata`, the root-symlink rejection message, and `subagents`. The selected
`identity` excerpt contains `sessionId`, `isSidechain`, and `session_meta`. These observations
corroborate the presence of scanner/path and transcript-identity logic in the compiled
artifact; they do not prove every branch implements the source specification correctly.

Reproduce from a PowerShell session in the repository root using the existing installation:

```powershell
$taskRoot = (Get-Location).Path
$env:JAVA_HOME = 'C:/Users/MattMangano/AppData/Local/Programs/ReverseEngineering/jdk-21.0.12.1+1'
$ghidraHeadless = 'C:/Users/MattMangano/AppData/Local/Programs/ReverseEngineering/ghidra_12.1.4_PUBLIC/support/analyzeHeadless.bat'
New-Item -ItemType Directory -Force -Path artifacts/ghidra | Out-Null
& $ghidraHeadless "$taskRoot/artifacts/ghidra" te-history-inspection `
  -import "$taskRoot/native/bin/te-history" `
  -scriptPath "$taskRoot/scripts" `
  -postScript InspectHistory.java "$taskRoot/artifacts/ghidra/report.txt" `
  -analysisTimeoutPerFile 180 -max-cpu 2 `
  -log "$taskRoot/artifacts/ghidra/analysis.log" `
  -scriptlog "$taskRoot/artifacts/ghidra/script.log"
```

For the already-created project, reuse its analysis rather than importing again:

```powershell
& $ghidraHeadless "$taskRoot/artifacts/ghidra" te-history-inspection `
  -process te-history -noanalysis -scriptPath "$taskRoot/scripts" `
  -postScript InspectHistory.java "$taskRoot/artifacts/ghidra/report.txt" `
  -log "$taskRoot/artifacts/ghidra/report-analysis.log" `
  -scriptlog "$taskRoot/artifacts/ghidra/report-script.log"
```

The second command produced the final report after narrowing function selection to the
scanner's own functions. The reusable source is `scripts/InspectHistory.java`; the project,
report, and logs stay under the existing ignored `artifacts/` directory. Verify the binary
hash before comparing this report with any later build.

Limitations: this was static analysis and did not execute the helper. Ghidra reported
missing external DWARF data, two unsupported thread-local symbols, 66 unresolved external
symbols because libc/libgcc were not imported, exception-table body/landing-pad errors,
and disassembly/decompiler warnings elsewhere in the program. The overall analysis and
both selected decompilations reported success despite those diagnostics. The report is
bounded, inferred pseudocode rather than original Rust, and is not evidence of runtime
safety, complete control-flow recovery, Linux execution success, or measured performance.

### Final packaged musl artifact

REA also inspected the final artifact with `inspect_rust_binary`. Evidence
`ev_53cd80888d66c1a1ba8ac158d3b8d7145f6b0cd511eb3138e50e98b2a81f8773` is retained in
[`rea-rust-evidence.json`](rea-rust-evidence.json). It independently reports the matching
SHA-256, ELF/amd64, 64-bit little-endian identity and 576,640-byte size. No `.dep-v0` metadata
section was present, so REA did not recover a dependency inventory. Its rustc commit marker
is explicitly an inference from an embedded path, not compiler authentication. Cargo.lock
and the copied dependency notices supply build-time dependency provenance separately.

The final package replaces the preceding development binary with the musl build. Its
authoritative identity is SHA-256
`ea831ecf91748af44cfedf495d438fa4a81238f37059d54fb21ffb15b429d118`, size 576,640 bytes.
Ghidra independently imported this binary into `te-history-musl-inspection`, reported ELF
`x86:LE:64:default` with the `gcc` ABI convention, and completed analysis and report export.
The image base is again `0x00100000`. It recognized 718 functions and 17 names containing
`te_history`; the initial glibc report's counts and addresses do not apply to this build.

The final report is `artifacts/ghidra/musl-report.txt`. It records successful decompilation
of `te_history::scan` at `0x00141ad0` (38,202 generated characters) and
`te_history::identity` at `0x00143720` (16,346 generated characters). Each retained excerpt
is explicitly truncated to 14,000 characters. The final scanner excerpt includes
`symlink_metadata` and the root-symlink rejection message; the identity excerpt includes
`sessionId`, `isSidechain`, and `session_meta`. This is bounded static corroboration of
compiled logic, not source-equivalence verification.

Using the PowerShell variables defined above, the command for the final binary is:

```powershell
& $ghidraHeadless "$taskRoot/artifacts/ghidra" te-history-musl-inspection `
  -import "$taskRoot/native/bin/te-history" `
  -scriptPath "$taskRoot/scripts" `
  -postScript InspectHistory.java "$taskRoot/artifacts/ghidra/musl-report.txt" `
  -analysisTimeoutPerFile 180 -max-cpu 2 `
  -log "$taskRoot/artifacts/ghidra/musl-analysis.log" `
  -scriptlog "$taskRoot/artifacts/ghidra/musl-script.log"
```

Use `-process te-history -noanalysis` instead of `-import ...` when reusing that existing
project. The final analysis also reported missing DWARF, unsupported thread-local symbols,
exception-table warnings, and disassembly/decompiler warnings elsewhere. Both selected
decompilations completed without their own error diagnostic. The same static-only and
truncation limitations apply. The final binary hash was checked before analysis; the
project retains its imported bytes independently of future builds at the source path.
