#!/usr/bin/env python3
"""The suite gate: what CI and the pre-push hook run instead of a bare
``python -m unittest discover``.

It discovers the same tests that command would, drops exactly the modules
listed in ``suite_exclusions.json`` (each with a recorded, human-readable
reason), and fails loudly - rather than silently widening its own pass rate -
if an exclusion entry no longer matches any discovered test, which usually
means the module was renamed, removed, or fixed and the entry is now stale.

The test tree lives in tests/ at the repo root (the runtime itself lives in
tools/, importable by its top-level module names because tests/__init__.py
puts tools/ on sys.path). This script stays at tools/run_suite.py, as every
doc and CI step already invokes it there, but discovers tests/ and reads
tests/suite_exclusions.json.

Usage:
    python3 tools/run_suite.py                  # discover tests/test_*.py, apply exclusions, run
    python3 tools/run_suite.py --jobs 1          # the same, in one process (the old serial run)
    python3 tools/run_suite.py --changed         # only the tests for what changed since origin/master
    python3 tools/run_suite.py --changed --include-slow   # the same, with the slow end-to-end modules
    python3 tools/run_suite.py --record-map      # run every test and record which files each one runs
    python3 tools/run_suite.py --list-excluded   # print excluded modules and reasons, run nothing
    python3 tools/run_suite.py --scenario-harness  # scenarios/test_harness.py, one test per process

By default each test module runs in its own interpreter, one per CPU at a time.
Most of the suite's time is spent waiting on subprocesses and timeouts, so
running modules side by side cuts the wall time several times over.

--changed runs the tests for the files changed since a base (committed,
staged, unstaged and untracked): a changed test module, the tests named after a
changed tools/ module (autocode_review_job.py -> test_review_job*), the tests
that import it directly, the tests that mention a changed non-Python tools/
file by name, and always test_architecture. A change to the suite machinery
itself runs everything. It does not follow imports transitively: most of
tools/ is one import cycle, so that would select almost every test.

Those rules cannot see a test that drives the CLI as a separate process: it
imports scenarios.harness, not the tools/ module it exercises, so a change to
that module used to skip it and the break first showed on master (#232, #242,
#330). So --changed also reads the test map, when there is one, and runs every
test recorded as running a changed file. --record-map PATH (default
.suite-map.json) runs the tests with tools/suite_trace/ on PYTHONPATH, which
records in each test's processes, CLI subprocesses included, the repository
files whose functions ran, then writes {test module: [files]} to PATH. The
nightly workflow .github/workflows/test-map.yml records it and caches it; a
pull request's CI restores the newest one. Tracing slows tests by about a
third, which is why master's own run does not record. A test missing from the
map is selected by the rules above only. A file the map lists that a change
has since stopped using only selects a test too many.

--changed also leaves out the slow end-to-end modules listed, with their CI
time, in tests/suite_slow.json (over 10 s each: they start the CLI, Git and fake
models as real processes), unless the module itself changed or --include-slow
is given. They run on master. Most tests are fast; those 19 modules are 345
tests and about three quarters of the suite's time.

Exit code is 0 only when every non-excluded test passes (or is itself
skipped by its own test-level skip guard) and every exclusion entry matched
at least one discovered test.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
TESTS_DIR = REPO_ROOT / "tests"
DEFAULT_EXCLUSIONS_PATH = TESTS_DIR / "suite_exclusions.json"
DEFAULT_SLOW_PATH = TESTS_DIR / "suite_slow.json"
DEFAULT_MAP_PATH = REPO_ROOT / ".suite-map.json"
TRACE_HOOK = HERE / "suite_trace"


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
    prefix), so excluding tests.test_a cannot silently also exclude
    tests.test_a_extra.
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


def discover(start_dir: Path = TESTS_DIR, top_level_dir: Path = REPO_ROOT) -> unittest.TestSuite:
    return unittest.defaultTestLoader.discover(str(start_dir), pattern="test_*.py",
                                               top_level_dir=str(top_level_dir))


ALWAYS = ("tests.test_architecture",)
# A change here can change which tests run or how, so it runs the whole suite.
FULL_SUITE_TRIGGERS = ("tools/run_suite.py", "tests/__init__.py", "tests/suite_exclusions.json",
                       "tests/suite_slow.json", "pyproject.toml", ".github/workflows/")


def imported_names(source: str) -> set[str]:
    """The dotted names a test module imports, as tools/ modules: `from units import autoreview`
    gives units and units.autoreview."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return {name.removeprefix("autocode_cli.").removeprefix("tools.") for name in names}


def tools_module(path: str) -> str | None:
    """tools/units/autoreview.py -> units.autoreview; None for anything else."""
    if not path.startswith("tools/") or not path.endswith(".py") or path.startswith("tools/dashboard/"):
        return None
    module = path[len("tools/"):-len(".py")].replace("/", ".")
    return module.removesuffix(".__init__")


def select_tests(changed: list[str], sources: dict[str, str],
                 runs: dict[str, set[str]] | None = None) -> dict[str, str] | None:
    """{test module: why it runs} for the changed paths, or None when everything must run.

    ``sources`` maps each test module (tests.test_x) to its source text; ``runs``, the test map,
    maps a test module to the repository files it was recorded running."""
    if any(path.startswith(FULL_SUITE_TRIGGERS) for path in changed):
        return None
    imports = {test: imported_names(source) for test, source in sources.items()}
    selected = {test: "always" for test in ALWAYS if test in sources}
    for path in changed:
        module = tools_module(path)
        if path.startswith("tests/test_") and path.endswith(".py"):
            test = "tests." + Path(path).stem
            if test in sources:
                selected[test] = "changed"
        elif module:
            stem = module.split(".")[-1].removeprefix("autocode_")
            for test in sources:
                name = test.removeprefix("tests.")
                if name == f"test_{stem}" or name.startswith(f"test_{stem}_") or module in imports[test]:
                    selected.setdefault(test, f"tests {path}")
        elif path.startswith("tools/") and not path.startswith("tools/dashboard/"):
            for test, source in sources.items():
                if Path(path).name in source:
                    selected.setdefault(test, f"mentions {path}")
        for test, ran in (runs or {}).items():
            if path in ran and test in sources:
                selected.setdefault(test, f"runs {path}")
    return selected


def load_map(path: Path) -> tuple[dict[str, set[str]], str]:
    """The test map at ``path`` and the commit it was recorded at; ({}, "") if there is none."""
    if not path.is_file():
        return {}, ""
    document = json.loads(path.read_text())
    return {test: set(files) for test, files in document["tests"].items()}, document.get("commit", "")


def traced_paths(directory: Path) -> list[str]:
    """Every repository file the processes traced into ``directory`` ran, sorted."""
    return sorted({line for record in directory.glob("*.txt") for line in record.read_text().splitlines() if line})


def write_map(path: Path, recorded: dict[str, list[str]], modules: list[str]) -> None:
    """Merge newly recorded tests into the map at ``path``, dropping tests that no longer exist."""
    previous = json.loads(path.read_text())["tests"] if path.is_file() else {}
    tests = {test: files for test, files in {**previous, **recorded}.items() if test in modules}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    path.write_text(json.dumps({"commit": commit, "tests": tests}, indent=1, sort_keys=True) + "\n")


def drop_slow(selected: dict[str, str], slow: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Leave out the slow modules a change selected, except one whose own file changed."""
    skipped = sorted(module for module, why in selected.items() if module in slow and why != "changed")
    return {module: why for module, why in selected.items() if module not in skipped}, skipped


def changed_paths(base: str) -> list[str]:
    """Files changed since the merge base with ``base``, including uncommitted and untracked ones."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.split()
    merge_base = git("merge-base", base, "HEAD")[0]
    return sorted(set(git("diff", "--name-only", merge_base)) | set(git("ls-files", "--others", "--exclude-standard")))


def test_modules(exclusions: dict[str, str]) -> list[str]:
    """Every tests/test_*.py module not excluded, largest file first, so the slow ones start early.

    Taken from the files, not from the discovered tests, so a module that fails to import still runs
    (and fails) in its own process instead of disappearing from the list."""
    paths = sorted(TESTS_DIR.glob("test_*.py"), key=lambda path: (-path.stat().st_size, path.name))
    return [module for module in (f"tests.{path.stem}" for path in paths) if module not in exclusions]


HARNESS = "scenarios.test_harness"


def harness_tests() -> list[str]:
    """scenarios/test_harness.py's tests, each to run in its own interpreter (importing the file
    takes about 0.2 s), so the slowest single test, not the slowest class, bounds the run.

    Loaded the way ``python -m unittest`` loads the file, so no test is left out; a module that
    fails to load is returned whole, to run (and fail) in its own process."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        tests = [test.id() for test in iter_tests(unittest.defaultTestLoader.loadTestsFromName(HARNESS))]
    except Exception:  # noqa: BLE001 - the whole-module run reports it
        return [HARNESS]
    if not tests or any(test.startswith("unittest.loader._FailedTest") for test in tests):
        return [HARNESS]
    return tests


def run_module(module: str, verbosity: int, trace: Path | None = None) -> dict:
    """Run one test module in its own interpreter from the repository root; with ``trace``, record
    the repository files its processes run under trace/<module>."""
    started = time.monotonic()
    command = [sys.executable, "-m", "unittest"] + (["-v"] if verbosity > 1 else []) + [module]
    env = None
    if trace is not None:
        (trace / module).mkdir(parents=True)
        env = {**os.environ, "AUTOCODE_SUITE_TRACE": str(trace / module),
               "PYTHONPATH": os.pathsep.join(filter(None, [str(TRACE_HOOK), os.environ.get("PYTHONPATH")]))}
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    ran = re.search(r"^Ran (\d+) tests?", completed.stderr, re.MULTILINE)
    row = {"module": module, "ok": completed.returncode == 0, "seconds": time.monotonic() - started,
           "tests": int(ran.group(1)) if ran else 0, "output": completed.stdout + completed.stderr}
    if trace is not None:
        row["ran"] = traced_paths(trace / module)
    return row


def run_parallel(modules: list[str], jobs: int, verbosity: int, unit: str = "modules",
                 record: Path | None = None) -> bool:
    """Run each module in its own process, ``jobs`` at a time; print a failing module's whole output.

    With ``record``, also write the test map there: the files each module's processes ran."""
    started = time.monotonic()
    failed = []
    tests = 0
    recorded: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="suite-trace-") as scratch, ThreadPoolExecutor(jobs) as pool:
        trace = Path(scratch) if record else None
        for future in as_completed([pool.submit(run_module, module, verbosity, trace) for module in modules]):
            row = future.result()
            tests += row["tests"]
            if record:
                recorded[row["module"]] = row["ran"]
            print(f"{'ok  ' if row['ok'] else 'FAIL'} {row['seconds']:6.1f}s  {row['module']} ({row['tests']} tests)",
                  flush=True)
            if verbosity > 1 and row["ok"]:
                print(row["output"], flush=True)
            if not row["ok"]:
                failed.append(row)
    for row in failed:
        print(f"\n{'=' * 70}\nFAIL: {row['module']}\n{'=' * 70}\n{row['output']}")
    print(f"\nRan {tests} tests in {len(modules)} {unit}, {jobs} at a time, in {time.monotonic() - started:.0f}s: "
          + (f"{len(failed)} module(s) FAILED: " + ", ".join(row["module"] for row in failed) if failed else "OK"))
    if record:
        write_map(record, recorded, test_modules(load_exclusions(DEFAULT_EXCLUSIONS_PATH)))
        print(f"Recorded the files {len(recorded)} test module(s) ran in {record}")
    return not failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS_PATH,
                         help="path to the exclusions JSON file (default: tests/suite_exclusions.json)")
    parser.add_argument("--list-excluded", action="store_true",
                         help="print excluded modules and reasons, then exit without running anything")
    parser.add_argument("--verbosity", type=int, default=1)
    parser.add_argument("--durations", type=int, metavar="N",
                        help="report the N slowest tests (Python 3.12+; runs in one process)")
    parser.add_argument("--changed", nargs="?", const="origin/master", metavar="BASE",
                        help="run only the tests for files changed since BASE (default origin/master)")
    parser.add_argument("--include-slow", action="store_true",
                        help="with --changed, also run the slow modules listed in tests/suite_slow.json")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP_PATH, metavar="PATH",
                        help="with --changed, the test map to select by (default .suite-map.json)")
    parser.add_argument("--record-map", nargs="?", type=Path, const=DEFAULT_MAP_PATH, metavar="PATH",
                        help="record which repository files each test module runs into PATH "
                             "(default .suite-map.json), merging into a map already there")
    parser.add_argument("--scenario-harness", action="store_true",
                        help="run scenarios/test_harness.py instead, each test in its own interpreter")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, metavar="N",
                        help="test modules to run at once, each in its own interpreter "
                             "(default: one per CPU; 1 runs everything in this process)")
    args = parser.parse_args(argv)

    if args.scenario_harness:
        return 0 if run_parallel(harness_tests(), max(args.jobs, 1), args.verbosity, "processes") else 1

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

    modules = test_modules(exclusions)
    slow = load_exclusions(DEFAULT_SLOW_PATH)
    stale_slow = sorted(set(slow) - set(modules))
    if stale_slow:
        print(f"STALE SLOW ENTRY: {stale_slow} in {DEFAULT_SLOW_PATH} matched no test module; "
              "remove it or fix the module name.", file=sys.stderr)
        return 2
    if args.changed:
        changed = changed_paths(args.changed)
        sources = {module: REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py").read_text()
                   for module in modules}
        runs, commit = load_map(args.map)
        print(f"Test map: {args.map}, recorded at {commit[:12] or 'an unknown commit'}, {len(runs)} test modules"
              if runs else f"No test map at {args.map}: selecting by imports and names only "
                           "(--record-map records one)")
        selected = select_tests(changed, sources, runs)
        skipped: list[str] = []
        if selected is not None and not args.include_slow:
            selected, skipped = drop_slow(selected, slow)
        if selected is None:
            print(f"{len(changed)} file(s) changed since {args.changed}, including the suite machinery: "
                  "running every test module.\n")
        else:
            print(f"{len(changed)} file(s) changed since {args.changed}; running {len(selected)} of "
                  f"{len(modules)} test modules:")
            for module, why in sorted(selected.items()):
                print(f"  - {module}: {why}")
            if skipped:
                print(f"Left out {len(skipped)} slow end-to-end module(s); they run on master "
                      f"(--include-slow runs them now): {', '.join(skipped)}")
            print()
            modules = [module for module in modules if module in selected]
            kept = unittest.TestSuite(test for test in iter_tests(kept) if _module_of(test.id()) in selected)

    if args.record_map:
        return 0 if run_parallel(modules, max(args.jobs, 1), args.verbosity, record=args.record_map) else 1
    if args.jobs > 1 and not args.durations:
        return 0 if run_parallel(modules, args.jobs, args.verbosity) else 1

    options = {"durations": args.durations} if args.durations else {}
    runner = unittest.TextTestRunner(verbosity=args.verbosity, **options)
    result = runner.run(kept)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
