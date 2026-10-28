#!/usr/bin/env python3
"""Copy locally resolved Cargo dependency notices into the distributable source tree."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
metadata = json.loads(subprocess.check_output([
    "cargo", "metadata", "--manifest-path", str(ROOT / "native/Cargo.toml"),
    "--locked", "--format-version", "1"], text=True))
out = ROOT / "native/licenses"
out.mkdir(parents=True, exist_ok=True)
index = ["# Native dependency notices", "", "Resolved versions are pinned in Cargo.lock.", ""]
for package in metadata["packages"]:
    if package["name"] == "te-history":
        continue
    tag = package["name"] + "-" + package["version"]
    source = Path(package["manifest_path"]).parent
    dest = out / tag
    dest.mkdir(exist_ok=True)
    copied = []
    for path in source.iterdir():
        if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT")):
            shutil.copyfile(path, dest / path.name)
            copied.append(path.name)
    if not copied:
        raise RuntimeError(f"No license text found for {tag}")
    index.append(f"- {tag}: {package.get('license')}; notices in `{tag}/`.")
index += ["", "The portable static build also incorporates musl (see `musl-COPYRIGHT`).",
          "Rust's standard library is distributed under MIT or Apache-2.0; the repository",
          "LICENSE supplies the Apache-2.0 terms. The scanner itself is Apache-2.0.", ""]
(out / "README.md").write_text("\n".join(index), encoding="utf-8")
