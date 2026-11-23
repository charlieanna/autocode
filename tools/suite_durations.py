#!/usr/bin/env python3
"""Refresh tests/suite_durations.json from run_suite.py logs, so --shard keeps its slices even.

    python3 tools/suite_durations.py LOG [LOG ...]

Each LOG is the output of ``run_suite.py`` (a downloaded CI job log works: timestamps are
ignored). Every finished row (``ok   12.3s  tests.test_x (5 tests)``) records that module's or
scenario test's seconds; a later log overrides an earlier one, and names no log mentions keep
their recorded value. The file only balances CI slices; it never decides what runs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "tests" / "suite_durations.json"
ROW = re.compile(r"(?:^|\s)(?:ok|FAIL|FAILED|ERROR)?\s+([0-9]+(?:\.[0-9]+)?)s\s+((?:tests|scenarios)\.[\w.\-]+) \(")


def parse(text: str) -> dict[str, float]:
    """Seconds per module or scenario test named in one run_suite.py log."""
    seconds = {}
    for line in text.splitlines():
        match = ROW.search(line)
        if match:
            seconds[match.group(2)] = round(float(match.group(1)), 1)
    return seconds


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    document = json.loads(PATH.read_text()) if PATH.exists() else {}
    seconds = dict(document.get("seconds", {}))
    for name in argv:
        seconds.update(parse(Path(name).read_text(errors="replace")))
    document["seconds"] = dict(sorted(seconds.items()))
    PATH.write_text(json.dumps(document, indent=1) + "\n")
    print(f"{len(seconds)} durations in {PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
