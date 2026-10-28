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
    python3 tools/run_suite.py --changed --all-fast       # every fast module, plus slow ones that changed
    python3 tools/run_suite.py --list-excluded   # print excluded modules and reasons, run nothing
    python3 tools/run_suite.py --scenario-harness  # scenarios/test_*.py, one test per process

By default each test module runs in its own interpreter, one per CPU at a time.
Most of the suite's time is spent waiting on subprocesses and timeouts, so
running modules side by side cuts the wall time several times over. A failing
module's whole output is printed as soon as it finishes, so a slow or hung
module still running cannot hide it. Its failures are printed again before the
summary, where a CI log opens: unittest's report from the first FAIL, ERROR or
UNEXPECTED SUCCESS on, or the whole output of a module that stopped before its
report (#545).

--changed runs the tests for the files changed since a base (committed,
staged, unstaged and untracked): a changed test module, the tests named after a
changed tools/ module (autocode_review_job.py -> test_review_job*), the tests
that import it directly, the tests that mention a changed resource by name,
explicit resource/public-CLI consumers, and always test_architecture. Suite
machinery changes select the gate's own controls and small public contracts.
It does not follow imports transitively, which
would select almost every test. A break that crosses modules is caught by the
full run on master.

Those rules cannot see a test that drives the CLI as a separate process: it
imports scenarios.harness, not the tools/ module it exercises, so a change to
that module skips it and the break first shows on master (#232, #242, #330).
--all-fast remains an opt-in to run every fast module instead. PR CI uses the
focused rules and explicit fake CLI consumers in ci_test_selection.
Recording which files each test actually runs did not help: the core tools/
modules run inside almost every test, so a typical change selected about 88%
of the fast modules' time anyway.

--changed also leaves out the slow end-to-end modules listed, with their CI
time, in tests/suite_slow.json (over 10 s each: they start the CLI, Git and fake
models as real processes), unless the module itself changed or --include-slow
is given, or an explicit public-consumer dependency requires it. They run on
master. Most tests are fast; the modules on that list take
about three quarters of the suite's time.

--scenario-harness runs scenarios/ instead: test_harness.py, the other test
modules there (adversarial attacks, native runs, human stops), and registered
catalog CLI checks, each test in its own interpreter. With --changed it selects
changed scenario modules/catalogs, lifecycle consumers and the modules that copy
a changed tools/ script (SCENARIO_FIXTURES); shared harness changes still run
every scenario module. Master runs all of them (#1088).

Exit code is 0 only when every non-excluded test passes (or is itself
skipped by its own test-level skip guard) and every exclusion entry matched
at least one discovered test.
"""

from __future__ import annotations

import argparse
import ast
import compileall
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import ci_test_selection

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
TESTS_DIR = REPO_ROOT / "tests"
DEFAULT_EXCLUSIONS_PATH = TESTS_DIR / "suite_exclusions.json"
DEFAULT_SLOW_PATH = TESTS_DIR / "suite_slow.json"


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


def filter_excluded(
    suite: unittest.TestSuite, exclusions: dict[str, str]
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
    return unittest.defaultTestLoader.discover(str(start_dir), pattern="test_*.py", top_level_dir=str(top_level_dir))


ALWAYS = ("tests.test_architecture",)
REQUIRED = "required consumer of "


def imported_names(source: str, package: str = "") -> set[str]:
    """The dotted names a test module imports, as tools/ modules: `from units import autoreview`
    gives units and units.autoreview."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if not package:
                    continue
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join([*parent, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module:
                names.add(module)
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return {name.removeprefix("autocode_cli.").removeprefix("tools.") for name in names}


def tools_module(path: str) -> str | None:
    """tools/units/autoreview.py -> units.autoreview; None for anything else."""
    if not path.startswith("tools/") or not path.endswith(".py") or path.startswith("tools/dashboard/"):
        return None
    module = path[len("tools/") : -len(".py")].replace("/", ".")
    return module.removesuffix(".__init__")


def select_tests(changed: list[str], sources: dict[str, str]) -> dict[str, str]:
    """{test module: why it runs} for changed paths, including dynamic consumers.

    ``sources`` maps each test module (tests.test_x) to its source text."""
    imports = {test: imported_names(source, test.rpartition(".")[0]) for test, source in sources.items()}
    selected = {test: "always" for test in ALWAYS if test in sources}
    for path in changed:
        module = tools_module(path)
        if path.startswith("tests/test_") and path.endswith(".py"):
            test = "tests." + Path(path).stem
            if test in sources:
                selected[test] = "changed"
        if module:
            stem = module.split(".")[-1].removeprefix("autocode_")
            for test in sources:
                name = test.removeprefix("tests.")
                if name == f"test_{stem}" or name.startswith(f"test_{stem}_") or module in imports[test]:
                    selected.setdefault(test, f"tests {path}")
            if not ci_test_selection.dependencies(path) and path not in ci_test_selection.SUITE_PATHS:
                for test in (*ci_test_selection.CORE_CONSUMERS, *ci_test_selection.PUBLIC_FLOW_CONSUMERS):
                    if test in sources:
                        selected.setdefault(test, REQUIRED + path)
        elif path.endswith(".py") and path.startswith(("tests/", "scenarios/harness/")):
            helper = path[:-3].replace("/", ".").removesuffix(".__init__")
            for test in sources:
                if helper in imports[test] or Path(path).name in sources[test]:
                    selected.setdefault(test, REQUIRED + path)
        elif path.startswith(("tools/", "tests/")) and not path.startswith("tools/dashboard/"):
            for test, source in sources.items():
                if Path(path).name in source:
                    selected.setdefault(test, f"mentions {path}")
        for test in ci_test_selection.dependencies(path) & sources.keys():
            if selected.get(test) != "changed":
                selected[test] = REQUIRED + path
    return selected


def drop_slow(selected: dict[str, str], slow: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Leave out indirect slow modules, retaining changed and explicit public consumers."""
    skipped = sorted(
        module
        for module, why in selected.items()
        if module in slow and why != "changed" and not why.startswith(REQUIRED)
    )
    return {module: why for module, why in selected.items() if module not in skipped}, skipped


def select_all(modules: list[str], selected: dict[str, str]) -> dict[str, str]:
    """Every test module, keeping the reason a change selected it (so a changed slow module still runs)."""
    return {module: selected.get(module, "all fast") for module in modules}


def changed_paths(base: str) -> list[str]:
    """Files changed since the merge base with ``base``, including uncommitted and untracked ones."""

    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, check=True).stdout

    merge_base = git("merge-base", base, "HEAD").decode("ascii").strip()
    paths = git("diff", "--no-renames", "--name-only", "-z", merge_base)
    paths += git("ls-files", "--others", "--exclude-standard", "-z")
    return sorted({path.decode("utf-8", errors="surrogateescape") for path in paths.split(b"\0") if path})


def test_modules(exclusions: dict[str, str]) -> list[str]:
    """Every tests/test_*.py module not excluded, largest file first, so the slow ones start early.

    Taken from the files, not from the discovered tests, so a module that fails to import still runs
    (and fails) in its own process instead of disappearing from the list."""
    paths = sorted(TESTS_DIR.glob("test_*.py"), key=lambda path: (-path.stat().st_size, path.name))
    return [module for module in (f"tests.{path.stem}" for path in paths) if module not in exclusions]


HARNESS = "scenarios.test_harness"
SCENARIOS_DIR = REPO_ROOT / "scenarios"
# Public CLI checks live beside their catalog fixture, outside top-level discovery.
# Keep this explicit: catalog oracles and unrelated standalone scripts are not suites.
CATALOG_TEST_MODULES = ("scenarios.catalog.progressive-learning-journey.test_cli",)


def split_module(module: str) -> list[str]:
    """A scenarios/ module's tests, each to run in its own interpreter (importing the file takes
    about 0.2 s), so the slowest single test, not the slowest class or module, bounds the run.

    Loaded the way ``python -m unittest`` loads the file, so no test is left out; a module that
    fails to load is returned whole, to run (and fail) in its own process."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        tests = [test.id() for test in iter_tests(unittest.defaultTestLoader.loadTestsFromName(module))]
    except Exception:  # noqa: BLE001 - the whole-module run reports it
        return [module]
    if not tests or any(test.startswith("unittest.loader._FailedTest") for test in tests):
        return [module]
    return tests


SHARED_SCENARIO_PATHS = ("scenarios/harness/", "scenarios/run.py", "scenarios/__init__.py")
# A tools/ script a scenario module copies by file name and runs, which no import names, and that
# module. ci_scope turns the scenario harness step on for these paths too.
SCENARIO_FIXTURES = {"tools/fake_opencode.py": "scenarios.test_contained_opencode"}
# Black-box consumers cannot import their runtime producers. Keep this map shared
# with ci_scope so selecting a consumer also enables the job that executes it.
SCENARIO_RUNTIME_CONSUMERS = {
    "tools/autocode_report_identity.py": ("scenarios.test_adversarial_evidence",),
    "tools/autocode_planning_advice.py": ("scenarios.test_adversarial_planning",),
    "tools/autocode_stuck_job.py": ("scenarios.test_adversarial_planning",),
    "tools/autocode_regression.py": (
        "scenarios.test_harness.StockRefusalsRunTests",
        "scenarios.test_harness.HybridRunTests",
    ),
    "tools/autocode_verify.py": (
        "scenarios.test_harness.StockRefusalsRunTests",
        "scenarios.test_harness.HybridRunTests",
    ),
}
LIFECYCLE_TOKENS = (
    "process",
    "session",
    "supervision",
    "keeper",
    "provider_launch",
    "build_loop",
    "run_setup",
    "run_actions",
)


def lifecycle_change(path: str) -> bool:
    return path.startswith("tools/") and any(token in Path(path).stem for token in LIFECYCLE_TOKENS)


def harness_tests(changed: list[str] | None = None) -> list[str]:
    """Harness cases, focused on small controls unless the shared harness itself changed."""
    tests = split_module(HARNESS)
    if changed is None or any(
        path.startswith(SHARED_SCENARIO_PATHS) or path == "scenarios/test_harness.py" for path in changed
    ):
        return tests
    classes = {"DriverFlagTests", "ModelProfileTests", "FakeSchemaTests", "ProcessDependencyTests"}
    if any(lifecycle_change(path) for path in changed):
        classes.update(
            {"DriverTimeoutTests", "HarnessNativeIdentityTests", "HarnessAttemptTests", "HarnessOwnerLossTests"}
        )
    classes.update(
        target.removeprefix(HARNESS + ".")
        for path in changed
        for target in SCENARIO_RUNTIME_CONSUMERS.get(path, ())
        if target.startswith(HARNESS + ".")
    )
    if any(path.startswith("scenarios/catalog/") for path in changed):
        classes.update({"CatalogTests", "OracleControlTests", "RunChecksTests"})
    # Failed imports must still run and fail, rather than disappear from focus.
    return [test for test in tests if test == HARNESS or "._FailedTest." in test or test.split(".")[-2] in classes]


def scenario_modules() -> list[str]:
    """Top-level scenario tests and registered catalog CLI modules. They drive the CLI as a black box,
    so no import rule selects them; until #1088 nothing ran them, and their expectations drifted."""
    paths = sorted(SCENARIOS_DIR.glob("test_*.py"), key=lambda path: (-path.stat().st_size, path.name))
    modules = [module for module in (f"scenarios.{path.stem}" for path in paths) if module != HARNESS]
    return [*modules, *CATALOG_TEST_MODULES]


def scenario_selection(changed: list[str] | None, slow: dict[str, str]) -> tuple[list[str], list[str]]:
    """Selected scenario modules and those left for master, including directly affected slow consumers.

    Shared harness changes can affect every scenario. An individual catalog or
    test change cannot enroll unrelated adversarial runs or the lying matrix.
    ``slow`` remains accepted for callers; explicit dependencies supersede it.
    """
    modules = scenario_modules()
    if changed is None or any(path.startswith(SHARED_SCENARIO_PATHS) for path in changed):
        return modules, []
    selected: set[str] = set()
    for path in changed:
        selected.update(SCENARIO_RUNTIME_CONSUMERS.get(path, ()))
        for module in modules:
            file = module.replace(".", "/") + ".py"
            if path == file or (module in CATALOG_TEST_MODULES and path.startswith(file.rsplit("/", 1)[0] + "/")):
                selected.add(module)
        if lifecycle_change(path):
            selected.add("scenarios.test_adversarial_lifecycle")
        if path == "scenarios/adversarial.py":
            selected.add("scenarios.test_adversarial_runner")
        if path in SCENARIO_FIXTURES:
            selected.add(SCENARIO_FIXTURES[path])
    return [module for module in modules if module in selected], [
        module for module in modules if module not in selected
    ]


# Since #1204 a Linux provider launch refuses a caller holding CAP_SYS_PTRACE or CAP_SYS_ADMIN
# (autocode_session_boundary). A Claude Code cloud container's root shell holds both; CI never does.
# So that the suite judges the code as CI does, each test interpreter runs with the two dropped from
# its bounding set (#668). Nothing changes where they are not held.
PRIVILEGED_CAPS = (1 << 19) | (1 << 21)  # CAP_SYS_PTRACE, CAP_SYS_ADMIN
DROP_PRIVILEGED = ("setpriv", "--bounding-set=-sys_admin,-sys_ptrace")


def privileged(status: str) -> bool:
    """Whether a /proc/<pid>/status text grants CAP_SYS_PTRACE or CAP_SYS_ADMIN (effective or permitted)."""
    held = 0
    for line in status.splitlines():
        if line.startswith(("CapEff:", "CapPrm:")):
            held |= int(line.partition(":")[2].strip() or "0", 16)
    return bool(held & PRIVILEGED_CAPS)


_PREFIX: tuple[str, ...] | None = None


def unprivileged_prefix() -> tuple[str, ...]:
    """The command prefix that runs a test interpreter without those capabilities, or () when none is needed."""
    global _PREFIX
    if _PREFIX is None:
        try:
            held = privileged(Path("/proc/self/status").read_text())
        except (OSError, ValueError):
            held = False
        _PREFIX = DROP_PRIVILEGED if held and shutil.which("setpriv") else ()
        if held and not _PREFIX:
            print(
                "run_suite: this process holds CAP_SYS_ADMIN or CAP_SYS_PTRACE and setpriv is missing; "
                "tests that launch a provider will stop at PAUSED_PROCESS_CLEANUP (#668).",
                file=sys.stderr,
            )
        elif held:
            print(
                "run_suite: running each test module without CAP_SYS_ADMIN and CAP_SYS_PTRACE, as CI does (#668).",
                file=sys.stderr,
            )
    return _PREFIX


def interpreter_roots() -> list[Path]:
    """This interpreter's sys.path directories outside the checkout, each nested one left to its parent: the trees
    verification hashes as dependency bytes (``autocode_verify.execution_identity``)."""
    repo = REPO_ROOT.resolve()
    roots = {Path(entry).resolve() for entry in sys.path if os.path.isdir(entry)}
    roots = {root for root in roots if not root.is_relative_to(repo) and not repo.is_relative_to(root)}
    return sorted(root for root in roots if not any(parent in roots for parent in root.parents))


def compile_runtime() -> None:
    """Write the bytecode of tools/ and of this interpreter's own trees once, before any test interpreter
    starts (#999).

    Each module, and every CLI process its tests launch, runs with bytecode writes off (``run_module``),
    so without a cache each of those processes compiled the ~330 tools/ modules the CLI imports from
    source: about 0.85 s of the 1.2 s of CPU a ``--status`` query took, which a loaded parallel run can
    stretch past a test's 10-second limit. Written here, before the run, the cache never changes while
    a test is measuring a tree. Python checks each cache against its source, so a source that changes
    later is compiled again, never served stale. A file that does not compile is left to its tests.

    A process started with -E or -I ignores PYTHONDONTWRITEBYTECODE, so a stale cache in the stdlib or
    site-packages (setup-python copies its stdlib with new timestamps) was rewritten during a check
    replay, which read it as a changed dependency. Nothing else in the checkout is compiled: tests
    snapshot it."""
    compileall.compile_dir(str(REPO_ROOT / "tools"), quiet=2)
    if os.name != "posix":
        return  # execution_identity reads os.uname(), so no check replay hashes these trees here
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the stdlib's own test data compiles with SyntaxWarnings
        for root in interpreter_roots():
            compileall.compile_dir(str(root), quiet=2)


def run_module(module: str, verbosity: int, timeout: float | None = None) -> dict:
    """Run one test module in its own interpreter from the repository root.

    With `timeout`, a module still running after that many seconds has its whole process tree ended and is
    reported as timed out (#1681), with the output it wrote so far, so one hung module cannot hold the run
    past its step limit and hide which module it was."""
    started = time.monotonic()
    command = [*unprivileged_prefix(), sys.executable, "-m", "unittest"]
    command += (["-v"] if verbosity > 1 else []) + [module]
    # Parallel imports must not change the shared dependency tree while another
    # test is measuring it for verification. Propagate this to test subprocesses
    # as well; a -B flag on the suite controller alone does not reach them.
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    timed_out = False
    returncode: int | None
    if timeout is None:
        completed = subprocess.run(command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True)
        returncode, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    else:
        returncode, stdout, stderr, timed_out = _run_bounded(command, environment, timeout)
    ran = re.search(r"^Ran (\d+) tests?", stderr, re.MULTILINE)
    output = stdout + stderr
    if timed_out:
        output += f"\nrun_suite: {module} was still running after {timeout:g}s; its process tree was ended.\n"
    return {
        "module": module,
        "ok": returncode == 0 and not timed_out,
        "timed_out": timed_out,
        "seconds": time.monotonic() - started,
        "tests": int(ran.group(1)) if ran else 0,
        "output": output,
    }


def _run_bounded(command: list[str], environment: dict, timeout: float) -> tuple[int | None, str, str, bool]:
    """Run `command` for at most `timeout` seconds in its own process group, ending the whole group on overrun.

    Output goes to files, not pipes: a grandchild that outlives the module (a server a test started) keeps a
    pipe open, so reading one would wait for it after the module itself was ended."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if sys.platform == "win32":
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                env=environment,
                stdout=out,
                stderr=err,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                command, cwd=REPO_ROOT, env=environment, stdout=out, stderr=err, start_new_session=True
            )
        try:
            returncode, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            _end_tree(process)
            returncode, timed_out = None, True
        out.seek(0)
        err.seek(0)
        return returncode, out.read().decode(errors="replace"), err.read().decode(errors="replace"), timed_out


def _end_tree(process: subprocess.Popen) -> None:
    """End a timed-out module and everything it started, then reap it."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        process.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()


# Where unittest's report of failures starts in a module's output: the separator above its first one.
FAILURE_REPORT = re.compile(rf"^{'=' * 70}\n(?:FAIL|ERROR|UNEXPECTED SUCCESS): ", re.MULTILINE)


def failure_report(output: str) -> str:
    """A failed module's output from unittest's first FAIL, ERROR or UNEXPECTED SUCCESS on, or all of it
    when there is none (the module crashed or exited early): what the end of the run shows again. The
    module's stdout comes first, so such a block printed there starts the repeat."""
    found = FAILURE_REPORT.search(output)
    return output[found.start() :] if found else output


def run_parallel(
    modules: list[str], jobs: int, verbosity: int, unit: str = "modules", timeout: float | None = None
) -> bool:
    """Run each module in its own process, ``jobs`` at a time. A failing module's whole output is printed
    as soon as it finishes, so a slow or hung module cannot hold back a finished failure (#545), and its
    failure report again at the end, so the tracebacks sit above the summary rather than under every later
    module's output. The repeat normally holds no per-test result line; a module that stopped before its
    report is repeated whole, and one that printed a report-like block to stdout from that block. Its
    headers name modules, not tests, and a repeated result line names a test already counted, so the
    per-module rows, the counts and the summary line read as before. AutoCode's verifier reads this
    output when a project declares run_suite as its suite command: it ends each traceback at its module's
    "Ran N tests ... FAILED" footer (``autocode_test_setup.failure_details``), so later modules' output
    never reads as part of one."""
    started = time.monotonic()
    failed = []
    tests = 0
    bound = [timeout] if timeout is not None else []
    unprivileged_prefix()  # once, before the workers race to compute it and each print its notice
    with ThreadPoolExecutor(jobs) as pool:
        for future in as_completed([pool.submit(run_module, module, verbosity, *bound) for module in modules]):
            row = future.result()
            tests += row["tests"]
            # A timed-out module is a failure; its row says so without changing the ok/FAIL column.
            limit = ", timed out" if row.get("timed_out") else ""
            print(
                f"{'ok  ' if row['ok'] else 'FAIL'} {row['seconds']:6.1f}s  {row['module']} ({row['tests']} tests{limit})",
                flush=True,
            )
            if verbosity > 1 and row["ok"]:
                print(row["output"], flush=True)
            if not row["ok"]:
                failed.append(row)
                print(f"\n{'=' * 70}\nFAIL: {row['module']}\n{'=' * 70}\n{row['output']}", flush=True)
    if failed:
        print("\nThe failures again:")
    for row in failed:
        print(f"\n{'=' * 70}\nFAIL: {row['module']}\n{'=' * 70}\n{failure_report(row['output'])}")
    timed_out = [row["module"] for row in failed if row.get("timed_out")]
    print(
        f"\nRan {tests} tests in {len(modules)} {unit}, {jobs} at a time, in {time.monotonic() - started:.0f}s: "
        + (f"{len(failed)} module(s) FAILED: " + ", ".join(row["module"] for row in failed) if failed else "OK")
        + (f" ({len(timed_out)} timed out: {', '.join(timed_out)})" if timed_out else "")
    )
    return not failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=DEFAULT_EXCLUSIONS_PATH,
        help="path to the exclusions JSON file (default: tests/suite_exclusions.json)",
    )
    parser.add_argument(
        "--list-excluded",
        action="store_true",
        help="print excluded modules and reasons, then exit without running anything",
    )
    parser.add_argument("--verbosity", type=int, default=1)
    parser.add_argument(
        "--durations", type=int, metavar="N", help="report the N slowest tests (Python 3.12+; runs in one process)"
    )
    parser.add_argument(
        "--changed",
        nargs="?",
        const="origin/master",
        metavar="BASE",
        help="run only the tests for files changed since BASE (default origin/master)",
    )
    parser.add_argument(
        "--all-fast",
        action="store_true",
        help="with --changed, run every test module except the slow ones that did not change",
    )
    parser.add_argument(
        "--include-slow",
        action="store_true",
        help="with --changed, also run the slow modules listed in tests/suite_slow.json",
    )
    parser.add_argument(
        "--scenario-harness",
        action="store_true",
        help="run scenario tests and registered catalog CLI checks, each test in its own interpreter "
        "(with --changed, only relevant consumers and harness controls)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        metavar="N",
        help="test modules to run at once, each in its own interpreter "
        "(default: one per CPU; 1 runs everything in this process)",
    )
    parser.add_argument(
        "--module-timeout",
        type=float,
        metavar="SECONDS",
        help="with --jobs above 1, end a test module still running after SECONDS and report it as a timed-out failure "
        "(default: no limit)",
    )
    args = parser.parse_args(argv)

    if args.scenario_harness:
        compile_runtime()
        changed = changed_paths(args.changed) if args.changed and not args.include_slow else None
        modules, skipped = scenario_selection(changed, load_exclusions(DEFAULT_SLOW_PATH))
        if skipped:
            print(
                f"Left out {len(skipped)} scenario module(s); they run on master "
                f"(--include-slow runs them now): {', '.join(skipped)}\n"
            )
        # The scenario modules' tests are the longer ones, so they start first.
        units = [test for module in modules for test in split_module(module)] + harness_tests(changed)
        return 0 if run_parallel(units, max(args.jobs, 1), args.verbosity, "processes") else 1

    exclusions = load_exclusions(args.exclusions)

    if args.list_excluded:
        if not exclusions:
            print("No exclusions configured.")
            return 0
        for module, reason in sorted(exclusions.items()):
            print(f"{module}: {reason}")
        return 0

    compile_runtime()  # before discovery imports the runtime too
    suite = discover()
    total_before = count_tests(suite)
    kept, matched, unmatched = filter_excluded(suite, exclusions)

    if unmatched:
        for module in sorted(unmatched):
            print(
                f"STALE EXCLUSION: {module!r} in {args.exclusions} matched no discovered test; "
                "remove it or fix the module name.",
                file=sys.stderr,
            )
        return 2

    if matched:
        print(
            f"Excluding {len(matched)} module(s) ({total_before - count_tests(kept)} test(s)), "
            f"each with a recorded reason (see --list-excluded):"
        )
        for module in sorted(matched):
            print(f"  - {module}: {exclusions[module]}")
        print()

    modules = test_modules(exclusions)
    slow = load_exclusions(DEFAULT_SLOW_PATH)
    stale_slow = sorted(set(slow) - set(modules) - set(scenario_modules()))
    if stale_slow:
        print(
            f"STALE SLOW ENTRY: {stale_slow} in {DEFAULT_SLOW_PATH} matched no test module; "
            "remove it or fix the module name.",
            file=sys.stderr,
        )
        return 2
    if args.changed:
        changed = changed_paths(args.changed)
        sources = {module: REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py").read_text() for module in modules}
        selected = select_tests(changed, sources)
        if args.all_fast:
            selected = select_all(modules, selected)
        skipped: list[str] = []
        if not args.include_slow:
            selected, skipped = drop_slow(selected, slow)
        print(
            f"{len(changed)} file(s) changed since {args.changed}; running {len(selected)} of "
            f"{len(modules)} test modules:"
        )
        for module, why in sorted(selected.items()):
            print(f"  - {module}: {why}")
        if skipped:
            print(
                f"Left out {len(skipped)} slow end-to-end module(s); they run on master "
                f"(--include-slow runs them now): {', '.join(skipped)}"
            )
        print()
        modules = [module for module in modules if module in selected]
        kept = unittest.TestSuite(test for test in iter_tests(kept) if _module_of(test.id()) in selected)

    if args.jobs > 1 and not args.durations:
        return 0 if run_parallel(modules, args.jobs, args.verbosity, timeout=args.module_timeout) else 1

    options = {"durations": args.durations} if args.durations else {}
    runner = unittest.TextTestRunner(verbosity=args.verbosity, **options)
    result = runner.run(kept)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
