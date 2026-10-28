#!/usr/bin/env python3
"""Export this working tree, including changes, with Git symlinks restored on Windows."""
import argparse
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def package(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).decode("utf-8").split("\0")
    indexed = subprocess.check_output(["git", "ls-files", "-s", "-z"], cwd=ROOT).decode("utf-8")
    links = {entry.split("\t", 1)[1] for entry in indexed.split("\0") if entry.startswith("120000 ")}
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(set(names) - {""}):
            path = ROOT / name
            if path == output or not path.exists():
                continue
            arcname = "tokeneconomizer-linux/" + name
            if name in links and not path.is_symlink():
                # Git core.symlinks=false checks a link out as its target text.
                info = tarfile.TarInfo(arcname)
                info.type, info.linkname, info.mode = tarfile.SYMTYPE, path.read_text().strip(), 0o777
                archive.addfile(info)
            elif name.endswith(".sh") or name in ("plugin/scripts/te", "plugin/scripts/te-hook"):
                data = path.read_bytes().replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(arcname)
                info.size, info.mode = len(data), 0o755
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.add(path, arcname=arcname, recursive=False)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=ROOT / "dist/tokeneconomizer-linux-source.tar.gz")
    print(package(parser.parse_args().out))
