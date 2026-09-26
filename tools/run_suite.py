#!/usr/bin/env python3
"""The suite gate: what CI and the pre-push hook run instead of a bare
``python -m unittest discover``.

It discovers the same tests that command would, drops exactly the modules
listed in ``suite_exclusions.json`` (each with a recorded, human-readable
reason), and fails loudly - rather than silently widening its own pass rate -
if an exclusion entry no longer matches any discovered test, which usually
means the module was renamed, removed, or fixed and the entry is now stale.

Usage:
    python3 tools/run_suite.py                  # discover tools/test_*.py, apply exclusions, run
    python3 tools/run_suite.py --list-excluded   # print excluded modules and reasons, run nothing

Exit code is 0 only when every non-excluded test passes (or is itself
skipped by its own test-level skip guard) and every exclusion entry matched
at least one discovered test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_EXCLUSIONS_PATH = HERE / "suite_exclusions.json"


def load_exclusions(path: Path) -> dict[str, str]:
    """Return {module_name: reason}, or {} if the file does not exist.

    The file is intentionally required to be a flat JSON object of
    string->string; anything else is almost certainly a mistake (an empty
    exclusions file should just be omitted or ``{}``), so it is rejected
    rather than silently ignored.
    """
    if not path.is_file():
        return {}
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"{path}: exclusions file must be a JSON object of module -> reason")
    for module, reason in document.items():
        if not isinstance(module, str) or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{path}: exclusion for {module!r} must be a non-empty string reason")
    return document


def iter_tests(suite: unittest.TestSuite):
    """Flatten a possibly-nested TestSuite into its individual test cases."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def count_tests(suite: unittest.TestSuite) -> int:
    return sum(1 for _ in iter_tests(suite))


def _module_of(test_id: str) -> str:
    # A unittest id is "<module.path>.<ClassName>.<method_name>"; the module
    # itself may contain dots, but class and method never do, so the module
    # is everything up to the second-to-last segment.
    return ".".join(test_id.split(".")[:-2])


def filter_excluded(suite: unittest.TestSuite, exclusions: dict[str, str]
                     ) -> tuple[unittest.TestSuite, set[str], set[str]]:
    """Split a discovered suite against an exclusion map.

    Returns (kept_suite, matched_modules, unmatched_modules). A module in
    ``exclusions`` is matched only by an exact module-path match (never a
    prefix), so excluding tools.test_a cannot silently also exclude
    tools.test_a_extra.
    """
    kept = unittest.TestSuite()
    matched: set[str] = set()
    for test in iter_tests(suite):
        module = _module_of(test.id())
        if module in exclusions:
            matched.add(module)
        else:
            kept.addTest(test)
    unmatched = set(exclusions) - matched
    return kept, matched, unmatched


def discover(start_dir: Path = HERE, top_level_dir: Path = REPO_ROOT) -> unittest.TestSuite:
    return unittest.defaultTestLoader.discover(str(start_dir), pattern="test_*.py",
                                               top_level_dir=str(top_level_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS_PATH,
                         help="path to the exclusions JSON file (default: tools/suite_exclusions.json)")
    parser.add_argument("--list-excluded", action="store_true",
                         help="print excluded modules and reasons, then exit without running anything")
    parser.add_argument("--verbosity", type=int, default=1)
    args = parser.parse_args(argv)

    exclusions = load_exclusions(args.exclusions)

    if args.list_excluded:
        if not exclusions:
            print("No exclusions configured.")
            return 0
        for module, reason in sorted(exclusions.items()):
            print(f"{module}: {reason}")
        return 0

    suite = discover()
    total_before = count_tests(suite)
    kept, matched, unmatched = filter_excluded(suite, exclusions)

    if unmatched:
        for module in sorted(unmatched):
            print(f"STALE EXCLUSION: {module!r} in {args.exclusions} matched no discovered test; "
                  "remove it or fix the module name.", file=sys.stderr)
        return 2

    if matched:
        print(f"Excluding {len(matched)} module(s) ({total_before - count_tests(kept)} test(s)), "
              f"each with a recorded reason (see --list-excluded):")
        for module in sorted(matched):
            print(f"  - {module}: {exclusions[module]}")
        print()

    runner = unittest.TextTestRunner(verbosity=args.verbosity)
    result = runner.run(kept)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
