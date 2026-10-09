#!/usr/bin/env python3
"""A scripted stand-in for a plain coding agent, for comparisons with no model spend.

Reads the brief on stdin like a real agent, ignores it, and lays the configured
solution over the current directory. Its result says nothing about model
quality; it proves the comparison's plumbing and scoring.
"""
import json
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    sys.stdin.read()
    config = json.loads(Path(os.environ["SCENARIO_FAKE_AGENT_CONFIG"]).read_text())
    solution = Path(config["solution"])
    for relative in config["paths"]:
        target = Path.cwd() / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(solution / relative, target)
    print(f"fake agent: applied {len(config['paths'])} files from {solution.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
