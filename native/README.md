# Native history discovery

`te-history` discovers old Claude Code and Codex session headers for the Python
warm-start importer. The existing Python mechanisms remain responsible for
repository isolation, confirmation counts, learning, tool pruning and import
idempotence. This binary does not call a model, execute transcript content, or
write state.

Build on Linux with Rust 1.74 or newer and a C linker:

```sh
cargo build --manifest-path native/Cargo.toml --locked --release
mkdir -p native/bin
cp native/target/release/te-history native/bin/te-history
native/bin/te-history scan --root "$HOME/.claude/projects" --host claude
native/bin/te-history scan --root "$HOME/.codex/sessions" --host codex
```

`CARGO_TARGET_DIR` changes the build output location. A glibc build depends on
the build host's compatible runtime; build on the destination machine or build
for a musl target when distributing a standalone binary.

The single stdout JSON array contains `path`, `cwd`, `session_id`, `mtime_ns`
(integer Unix nanoseconds), and `size` (bytes), ordered by modification time then
path. Missing roots return `[]`; invalid arguments, inaccessible files and
exceeded limits fail with a nonzero status and an explanation on stderr. No
partial result is emitted on a scan error.

Only complete newline-delimited records in each file's first 262,144 bytes are
examined. Claude identity must be top-level `cwd` and `sessionId` in the same
record. Codex identity must be in `session_meta.payload.cwd` and `.id`. `cwd`
must be absolute. Child-agent directories (`subagents`), Claude `agent-*`
paths and Claude headers marked `isSidechain: true` are excluded. File and
directory symlinks are skipped, and a symlink root is rejected. Unix file opens
also use `O_NOFOLLOW`. This is discovery of local history, not a filesystem
sandbox against concurrently hostile directory replacement.

Scanning defaults to at most 10,000 candidate JSONL files. Use `--max-files N`
to change the limit; crossing it fails explicitly so incomplete evidence is
not mistaken for a complete scan. Header truncation or an unrecognized schema
omits that file rather than guessing identity from its name or message text.

```sh
cargo test --manifest-path native/Cargo.toml --locked
cargo fmt --manifest-path native/Cargo.toml --check
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
```
