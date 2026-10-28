#!/bin/sh
# Build the Rust scanner and a self-contained Linux plugin marketplace.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
if [ -n "${TE_RUST_TARGET:-}" ]; then
  cargo build --manifest-path native/Cargo.toml --locked --release --target "$TE_RUST_TARGET"
  binary="${CARGO_TARGET_DIR:-native/target}/$TE_RUST_TARGET/release/te-history"
else
  cargo build --manifest-path native/Cargo.toml --locked --release
  binary="${CARGO_TARGET_DIR:-native/target}/release/te-history"
fi
mkdir -p native/bin
cp "$binary" native/bin/te-history
chmod 755 native/bin/te-history
python3 scripts/build_plugin.py --out dist/marketplace
chmod 755 dist/marketplace/tokeneconomizer/scripts/te dist/marketplace/tokeneconomizer/scripts/te-hook
tar -czf dist/tokeneconomizer-linux-marketplace.tar.gz -C dist marketplace
printf '%s\n' 'Built dist/tokeneconomizer-linux-marketplace.tar.gz'
