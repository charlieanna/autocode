"""Collect unittest identities in independent named/discovery interpreters.

Never executes test methods or emits a PASS receipt. Optional expected identities
are an operator-approved inventory, not an exclusion list or proof substitute.
Use in --task-preflight argv; every invocation uses the selected test interpreter.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import unittest


def leaves(suite):
    for child in suite:
        if isinstance(child, unittest.TestSuite):
            yield from leaves(child)
        else:
            yield child


def collect(mode, args):
    # Match `python -m unittest` from the project root, even when this helper
    # is addressed by its absolute immutable-runtime path.
    sys.path.insert(0, str(Path.cwd()))
    loader = unittest.TestLoader()
    if mode == "named":
        suite = loader.loadTestsFromNames(args.named)
    else:
        suite = loader.discover(args.discover, pattern=args.pattern, top_level_dir=args.top_level)
    identities = sorted(test.id() for test in leaves(suite))
    errors = list(loader.errors)
    if not identities:
        errors.append("No test identities collected")
    if len(identities) != len(set(identities)):
        errors.append("Duplicate test identities collected")
    return {"mode": mode, "status": "NOT_READY" if errors else "COLLECTION_READY", "identities": identities,
            "errors": errors, "tests_executed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--named", action="append", default=[])
    parser.add_argument("--discover")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--top-level")
    parser.add_argument("--expected-ids", type=Path, help="JSON object mapping named/discovery to exact sorted identities")
    parser.add_argument("--mode", choices=("named", "discovery"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.named and not args.discover:
        parser.error("Declare --named and/or --discover")
    if args.mode:
        try:
            row = collect(args.mode, args)
        except (ImportError, OSError, ValueError) as error:
            row = {"mode": args.mode, "status": "NOT_READY", "identities": [], "errors": [str(error)], "tests_executed": False}
        print("AUTOCODE_COLLECTION=" + json.dumps(row))
        return 0 if row["status"] == "COLLECTION_READY" else 1
    selected = argv if argv is not None else sys.argv[1:]
    rows = []
    for mode in (["named"] if args.named else []) + (["discovery"] if args.discover else []):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), *selected, "--mode", mode],
                               text=True, capture_output=True)
        lines = [line.removeprefix("AUTOCODE_COLLECTION=") for line in child.stdout.splitlines()
                 if line.startswith("AUTOCODE_COLLECTION=")]
        if child.returncode not in (0, 1) or len(lines) != 1:
            rows.append({"mode": mode, "status": "NOT_READY", "identities": [], "tests_executed": False,
                         "errors": ["Collection subprocess failed: " + (child.stderr or child.stdout)[-2000:]]})
        else:
            rows.append(json.loads(lines[0]))
    if args.expected_ids:
        expected = json.loads(args.expected_ids.read_text())
        if not isinstance(expected, dict) or set(expected) != {row["mode"] for row in rows}:
            parser.error("Expected inventory must name exactly the selected collection modes")
        for row in rows:
            if expected[row["mode"]] != row["identities"]:
                row["errors"].append("Collection differs from the approved identity inventory")
                row["status"] = "NOT_READY"
    ready = all(row["status"] == "COLLECTION_READY" for row in rows)
    print(json.dumps({"kind": "prerequisite", "status": "READY" if ready else "BLOCKED", "collections": rows,
                      "tests_executed": False}, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
