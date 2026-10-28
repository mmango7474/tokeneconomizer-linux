#!/usr/bin/env python3
"""Compare discovery only on synthetic history; does not measure model token savings."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokeneconomizer.plugin.history import discover, scan_python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.files < 1 or args.runs < 1:
        parser.error("files and runs must be positive")
    with tempfile.TemporaryDirectory(prefix="te-benchmark-") as tmp:
        for i in range(args.files):
            row = json.dumps(dict(cwd="/synthetic/repo", sessionId=f"session-{i}"))
            Path(tmp, f"{i}.jsonl").write_text(row + "\n" + '{"text":"' + "x" * 65536 + '"}\n')
        expected = scan_python(tmp, "claude")
        native, backend = discover(tmp, "claude", dict(os.environ))
        if backend != "rust":
            parser.error("build native/bin/te-history first")
        assert expected == native, "scanner outputs differ"
        timings = {"python": [], "rust": []}
        for _ in range(args.runs):
            for label, fn in (("python", lambda: scan_python(tmp, "claude")),
                              ("rust", lambda: discover(tmp, "claude", dict(os.environ))[0])):
                start = time.perf_counter()
                assert fn() == expected
                timings[label].append(time.perf_counter() - start)
        medians = {key: statistics.median(values) for key, values in timings.items()}
        print(json.dumps(dict(files=args.files, runs=args.runs, median_seconds=medians,
                              discovery_speedup=medians["python"] / medians["rust"],
                              note="Synthetic warm-cache discovery only; not token savings"), indent=2))


if __name__ == "__main__":
    main()
