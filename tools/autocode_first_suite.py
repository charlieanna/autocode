"""Preservation evidence when a pinned base has no behavior for the suite being proved.

A documentation-only tree, or a tree with no Go project when the suite is Go.
"""

import re
import subprocess
from pathlib import PurePosixPath

try:
    from . import autocode_changed_tests as changed_tests
    from . import autocode_project_inventory as project_inventory
    from . import autocode_project_kinds as project_kinds
    from . import autocode_test_support as test_support
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_changed_tests as changed_tests
    import autocode_project_inventory as project_inventory
    import autocode_project_kinds as project_kinds
    import autocode_test_support as test_support
    import autocode_util as util
    import autocode_verification_schedule as schedule


# `go test` says these when the directory is not a Go module or contains no packages.
# A package that failed to build does not.
_NO_GO_SUITE = (
    "matched no packages",
    "no packages to test",
    "does not contain main module",
    "go.mod file not found",
    "cannot find main module",
)
# The pattern itself, reported as a build failure when `go test` is not inside a module.
_GO_PATTERN_FAILURES = frozenset({"./...::[build failed]", ".::[build failed]"})


def no_go_project(inventory, copied) -> bool:
    """A link, submodule or copied Go source keeps ordinary preservation rules."""
    paths = project_inventory.regular_paths(inventory)
    return paths is not None and not contains_go_project([*paths, *copied])


def contains_go_project(paths) -> bool:
    """True when any path is a Go module, a Go workspace, or Go source."""
    for path in paths:
        name = PurePosixPath(str(path))
        if name.name in ("go.mod", "go.work") or name.suffix == ".go":
            return True
    return False


# Project, dependency and license manifests: data naming tools and versions,
# not behavior a suite runs. A first project can commit them before any source
# exists (#1783).
MANIFESTS = frozenset(
    {
        *project_kinds.MANIFESTS,
        *project_kinds.LOCKFILES,
        "Pipfile",
        "Pipfile.lock",
        "pom.xml",
        "gradle.properties",
        "composer.json",
        "composer.lock",
        "pubspec.yaml",
        "pubspec.lock",
        "LICENSE",
        "LICENCE",
        "COPYING",
        "NOTICE",
        "MANIFEST.in",
        ".python-version",
        ".nvmrc",
        ".node-version",
    }
)
# C#, F# and Visual Basic project and solution files are XML or text manifests.
MANIFEST_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln")


# Plain text and data a deliverable reads: nothing runs it. A greenfield chat task commonly ships its own
# input beside the README (a word-frequency CLI and its sample.txt, #2036). Formats with an executable
# form (.sh, .py, templates) are not here; a data file counts only in a base with no code or test file.
DATA_SUFFIXES = (
    ".txt",
    ".text",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".xml",
    ".ini",
    ".dat",
    ".log",
    ".rst",
    ".adoc",
)


def data_file(path) -> bool:
    """A plain text or data file by its suffix: read by a program, never run by one."""
    return PurePosixPath(str(path)).suffix.lower() in DATA_SUFFIXES


# Extensionless files that a build or container tool runs by name.
BUILD_FILES = frozenset(
    {
        "Makefile",
        "GNUmakefile",
        "makefile",
        "Dockerfile",
        "Containerfile",
        "Procfile",
        "Rakefile",
        "Justfile",
        "justfile",
        "Vagrantfile",
        "Jenkinsfile",
        "Brewfile",
        "Earthfile",
        "Tiltfile",
        "Snakefile",
        "BUILD",
        "WORKSPACE",
    }
)
NOTE_LIMIT = 4096


def plain_note(path, content) -> bool:
    """A short extensionless text file that nothing runs: a stray note a person left (#2259).

    Content decides, not the name: it must be UTF-8 text without NUL bytes or a
    ``#!`` line, at most NOTE_LIMIT bytes, and not a build file a tool runs by name.
    """
    name = PurePosixPath(str(path))
    if name.suffix or name.name.startswith(".") or name.name in BUILD_FILES or content is None:
        return False
    if len(content) > NOTE_LIMIT or b"\0" in content or content.lstrip().startswith(b"#!"):
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def blob_reader(workspace, revision):
    """``read(path)``: the bytes of path at revision, or None when Git cannot show it."""

    def read(path):
        shown = subprocess.run(
            ["git", "cat-file", "blob", f"{revision}:{path}"],
            cwd=workspace,
            capture_output=True,
            env=util.git_environment(),
        )
        return shown.stdout if shown.returncode == 0 else None

    return read


DESIGN_DIRECTORY = PurePosixPath("docs", "design")
PROSE_DOCUMENT_SUFFIXES = (".rst", ".adoc")


def non_markdown_design_document(path) -> bool:
    """A prose document in the design workflow's directory, in a format it never writes.

    The approved-design build accepts its own Markdown document as a base with no
    behavior. Another prose format there is a document, not data the deliverable
    reads, so it may not make the base document-only (#2069).
    """
    name = PurePosixPath(str(path))
    return name.suffix.lower() in PROSE_DOCUMENT_SUFFIXES and name.parent == DESIGN_DIRECTORY


def manifest(path) -> bool:
    """A project, dependency or license manifest: data, not source the suite runs."""
    name = PurePosixPath(str(path)).name
    return (
        name in MANIFESTS
        or (name.startswith("requirements") and name.endswith((".txt", ".in")))
        or name.endswith(MANIFEST_SUFFIXES)
    )


def test_harness(path) -> bool:
    """A pytest ``conftest.py``: configuration the test runner loads, never a test it collects.

    It holds no product behavior. In a base with no other code or test file it only prepares
    a suite that does not exist yet (a first project's ``sys.path`` setup).
    """
    return PurePosixPath(str(path)).name == "conftest.py"


def holds_source(regular, code_or_test) -> bool:
    """A nonempty code or test file among the base's (path, size) entries; a conftest.py configures a suite and is never one."""
    return any(size and code_or_test(path) and not test_harness(path) for path, size in regular)


def inert_base_file(path, size, *, separate, source, read=None) -> bool:
    """True when a pinned base entry holds no behavior a suite could run.

    README.md is always accepted. In a separate checkout (the candidate cannot
    have hidden ignored code) a .gitignore, a Markdown document and an empty
    file are accepted. So are a manifest and a plain text or data file
    (data_file), but only when no nonempty file in the base is code or a test
    path: a first project's manifest holds no behavior to preserve (#1783), and
    neither does the data its deliverable will read (#2036); a base that holds
    source still needs passing-test evidence. A non-Markdown prose document in
    the design workflow's directory is not one of those data files: the workflow
    writes Markdown, so another format there is not the approved design (#2069).
    In the same base, a short extensionless text note (plain_note, its content
    supplied by ``read``) holds no behavior either (#2259), nor does a pytest
    ``conftest.py`` (test_harness). ``source`` never counts a conftest.py itself.
    """
    if path == "README.md":
        return True
    if not separate:
        return False
    name = PurePosixPath(str(path))
    return (
        name.name == ".gitignore"
        or name.suffix == ".md"
        or size == 0
        or (not source and (manifest(path) or (data_file(path) and not non_markdown_design_document(path))))
        or (not source and read is not None and size <= NOTE_LIMIT and plain_note(path, read(path)))
        or (not source and test_harness(path))
    )


def absent_base_suite(base_receipt, candidate_receipt, *, document_only):
    """A new suite may not exist on base; the candidate must actually run it.

    The caller establishes document_only from the pinned Git inventory, matching
    baseline identity and command, and new-behavior mode without a base patch.
    This is evidence of no old behavior to preserve, not a successful base run.
    It never substitutes for the separate new-behavior proof.
    """
    return bool(
        document_only
        and base_receipt.get("results_expected") is True
        and base_receipt.get("results") is None
        and base_receipt.get("exit_code") == 1
        and base_receipt.get("timed_out") is False
        and _candidate_passed(candidate_receipt)
    )


def empty_collection(base_receipt) -> bool:
    """The base suite ran to completion and honestly collected no test.

    Python 3.14 unittest and pytest exit 5 for an empty collection; other runners exit 0.
    """
    results = base_receipt.get("results")
    return bool(
        results is not None
        and base_receipt.get("results_expected") is True
        and (
            base_receipt.get("exit_code") == 0
            or (
                base_receipt.get("exit_code") == 5
                and schedule.collection_kind(base_receipt.get("command", "")) in ("unittest", "pytest")
            )
        )
        and results.get("complete") is True
        and results.get("total") == 0
        and all(results.get(key) == [] for key in ("passed", "failed", "skipped", "collection_errors"))
    )


UNTESTED_BASE = (
    "The original code has no tests, so preservation of existing behavior is unproven; "
    "add tests of the existing behavior this change touches that pass on the original code and on the change"
)
UNTESTED_BASE_KEPT = "The original code has no tests; the change's tests that pass on it and on the change keep it"


def no_base_evidence(untested, *, ran=True) -> str:
    """Why preservation is unproven; for a base with no test, what the Builder can add to prove it."""
    if untested:
        return UNTESTED_BASE
    if not ran:
        return "The base suite could not run; preservation of existing behavior is unproven"
    return "The base suite provided no complete passing-test evidence; preservation of existing behavior is unproven"


def no_base_suite(base_receipt) -> bool:
    """The base had no suite to run: an empty collection, or a known collector that found nothing to start.

    A missing test directory makes unittest exit 1 with no per-test results (absent_base_suite).
    """
    return empty_collection(base_receipt) or bool(
        base_receipt.get("results_expected") is True
        and base_receipt.get("results") is None
        and base_receipt.get("exit_code") == 1
        and base_receipt.get("timed_out") is False
    )


def untested_base_kept(base_receipt, candidate_receipt, kept, *, untested) -> bool:
    """A base with code but no test file: the change's own tests stand in for the suite it lacks (#2259).

    ``kept`` are the change's tests that ran and passed on the original code and still pass
    (the proof's pass_to_pass). The base must have had no suite to run (no_base_suite), hold no
    test path (``untested``), and the candidate suite pass completely.
    """
    return bool(untested and kept and no_base_suite(base_receipt) and _candidate_passed(candidate_receipt))


def _candidate_passed(candidate_receipt):
    candidate = candidate_receipt.get("results") or {}
    return bool(
        candidate_receipt.get("timed_out") is False
        and candidate_receipt.get("exit_code") == 0
        and schedule.complete_results(candidate_receipt)
        and candidate.get("passed")
        and not candidate.get("failed")
        and not candidate.get("skipped")
    )


def go_reported_nothing(base_receipt):
    """The Go suite produced no package results, only the known no-project report."""
    text = base_receipt.get("tail") or ""
    if not any(phrase in text for phrase in _NO_GO_SUITE):
        return False
    results = base_receipt.get("results")
    if results is None:
        return True
    if results.get("passed") or results.get("skipped"):
        return False
    failed = set(results.get("failed") or [])
    return failed <= _GO_PATTERN_FAILURES


def absent_go_suite(base_receipt, candidate_receipt, *, no_go_project):
    """A Go suite may not exist on base; the candidate must actually run it.

    The caller establishes no_go_project from the pinned Git inventory (no
    ``go.mod``, ``go.work`` or ``.go`` file, and no submodule or link that could
    hide one) plus any ignored Go source copied into the proof trees. The base
    receipt must be the runner's no-package report, not a package that failed
    to build. This is evidence of no old Go behavior to preserve, not a
    successful base run. It never substitutes for the separate new-behavior proof.
    """
    return bool(
        no_go_project
        and go_reported_nothing(base_receipt)
        and base_receipt.get("results_expected") is True
        and base_receipt.get("exit_code") == 1
        and base_receipt.get("timed_out") is False
        and _candidate_passed(candidate_receipt)
    )


def repairable_failures(base_suite, suite_command):
    """The base suite's tests that ran and failed there, when its command is the proof's suite command.

    A repair whose regression test the project already had changes no test; whether these pass on the
    candidate (existing_flip) decides its proof. Collection errors are not tests that ran.
    """
    results = ((base_suite or {}).get("receipt") or {}).get("results")
    if not suite_command or (base_suite or {}).get("command") != suite_command:
        return []
    if not isinstance(results, dict) or not results.get("complete"):
        return []
    return sorted(set(results["failed"]) - set(results.get("collection_errors") or []))


def unloaded(base_suite, suite_command):
    """The base suite's test modules that could not be imported, collected or built there, when its command
    is the proof's suite command: its ``uncollected`` (``collection_errors`` for a parser without it).

    Code that cannot be loaded (a syntax error, merge conflict markers left in a file) lets no test run, so
    no test "ran and failed" there. A real run of "resolve the conflict markers, the tests already cover
    both sides" failed its proof twice for want of a new test, with every existing test passing, and the
    person was asked to set a regression command (2026-11-14). ``loaded`` says which tests show its repair.
    """
    results = ((base_suite or {}).get("receipt") or {}).get("results")
    if not suite_command or (base_suite or {}).get("command") != suite_command:
        return []
    if not isinstance(results, dict) or not results.get("complete"):
        return []
    return sorted(results.get("uncollected", results.get("collection_errors")) or [])


# A Cargo target that did not build: ``amounts (lib)``, ``amounts (lib test)``, ``amounts (test "pager")``.
_CARGO_TARGET = re.compile(r"[^\s:]+ \((?:lib|bin|test|example|bench|build script)\b[^)]*\)")


def _owns(module, test) -> bool:
    """Whether ``test`` is a test of the module the base could not load, by the ids the result parsers give:
    ``::pkg.mod`` and ``pkg.mod::test`` (pytest), ``unittest.loader._FailedTest.mod`` and ``mod.Class.test``
    (unittest), ``FILE::[collection]`` or ``PACKAGE::[build failed]`` and ``FILE::test`` (Node, Jest, RSpec,
    Minitest, Go). A JUnit test that errored before it ran is its own id and a failure there, so it is
    never a test that could not run.

    ``cargo test`` builds every target before it runs a test, so a target that did not build
    (``CRATE (lib)::[build failed]``) stopped every test of the run, and Cargo's test ids
    (``src/lib.rs::tests::one``, ``tests/pager.rs::one``) do not name a crate: every test is one it stopped."""
    for prefix in ("unittest.loader._FailedTest.", "unittest.loader.ModuleImportFailure."):
        if module.startswith(prefix):
            return test.startswith(module[len(prefix) :] + ".")
    key = module.removeprefix("::").partition("::[")[0]
    return bool(_CARGO_TARGET.fullmatch(key)) or test.startswith(key + "::")


def loaded(base_results, candidate, modules, added=()):
    """The tests that could not run on the base, whose ``modules`` it could not load (``unloaded``), and
    that run and pass on the candidate.

    They count only when the whole suite loads on the candidate (nothing uncollected, no collection error),
    nothing fails there that did not already run and fail on the base, and every one of ``modules`` has a
    test among them that the project already had, so the change did not trade one unloadable module for
    another or a failure, did not leave one of them out of the suite, and does not prove itself with its
    own tests alone. A test that ran on the base, or was skipped there, is not one of them. The caller has
    established that the change modifies and deletes no test file; ``added`` is the tests of the files it
    adds, which are among the result and are no module's own.
    """
    if candidate.get("uncollected") or candidate.get("collection_errors"):
        return []
    ran_and_failed = set(base_results["failed"]) - set(base_results.get("collection_errors") or [])
    if not set(candidate["failed"]) <= ran_and_failed:
        return []
    known = set(base_results["passed"]) | set(base_results["failed"]) | set(base_results.get("skipped") or [])
    new = sorted(set(candidate["passed"]) - known)
    own = set(new) - set(added)
    return new if all(any(_owns(module, test) for test in own) for module in modules) else []


def _added_tests(run):
    """The tests of the test files the change adds: those its targeted run on the candidate reported. None
    when that cannot be told: no targeted command of its own, or no per-test results.

    Go and Cargo run an added file with the rest of its package, so there only the tests the change itself
    defines are its own (autocode_changed_tests.changed_filter); Cargo's targeted command is the whole
    suite's, and that filter still tells them apart.
    """
    commands = run.commands
    results = (run.checks.get("regression_on_candidate") or {}).get("results")
    if not commands["regression"] or not isinstance(results, dict):
        return None
    reported = {*results["passed"], *results["failed"], *(results.get("skipped") or [])}

    def git(workspace, *arguments, check=False):
        shown = subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=check,
            env=util.git_environment(),
        )
        return shown.stdout

    changed = changed_tests.changed_filter(run, git)
    if changed:
        return {test for test in reported if changed(test)}
    return None if commands["regression"] == commands["suite"] else reported


def suite_on_candidate(run, trees, run_suite):
    """The whole suite on the candidate, run once in a proof: the targeted run when it is the same command."""
    checks, commands = run.checks, run.commands
    if "suite_on_candidate" not in checks:
        reuse = checks.get("regression_on_candidate") if commands["suite"] == commands["regression"] else None
        if reuse is None and getattr(run, "progress", None) is not None:
            run.progress(
                "Checking the full candidate suite",
                command=commands["suite"],
                output=run.run_dir,
                phase="candidate_suite",
            )
        checks["suite_on_candidate"] = reuse or run_suite(
            run.framework, commands["suite"], trees["candidate"], run.run_dir, "suite-on-candidate", timeout=run.timeout
        )
    return checks["suite_on_candidate"]


def reloaded(run, on_candidate) -> list[str]:
    """The tests that show the repair of code its tests could not load, for a change that adds tests.

    Owner decision, 2026-11-14: existing tests that now pass prove such a fix. The plan of a real run of
    "resolve the conflict markers" added one new test in a new file. The new test cannot run on the original
    code either (its import is the syntax error), a bug fix needs a test that ran and failed there, and the
    proof failed twice with "the new tests only fail to import or collect"; when the Builder worked around
    that with imports inside each test, preservation stayed unproven and the person was asked to accept it.

    The base suite itself must name modules it could not load (``unloaded``), and the change must modify and
    delete no test file: then ``loaded`` gives the tests that could not run on the original code and pass
    with the change, the project's own and the change's new ones. Each of those modules must have a test
    the project already had: one the targeted run of the added files did not report (``_added_tests``), so
    code that never had a test is not proved by the change's tests alone. Empty when the rule does not hold,
    and for a change that touches no test file, which existing_flip judges.
    """
    gone = unloaded(run.base_suite, run.commands["suite"])
    candidate = (on_candidate or {}).get("results")
    if not gone or run.existing_failures or not (candidate or {}).get("complete") or on_candidate.get("timed_out"):
        return []
    tests, added = run.roles.partition(run.changes)[0], _added_tests(run)
    # A Rust file may hold the change's new tests beside its code (run.runnable_tests): its own stay as they were.
    if not (tests or run.runnable_tests) or added is None or any(run.changes[path] != "added" for path in tests):
        return []
    if not changed_tests.inline_tests_kept(run.workspace, run.base, run.changes):
        return []
    return loaded(run.base_suite["receipt"]["results"], candidate, gone, added)


def reloads(run, trees, run_suite) -> list[str]:
    """``reloaded`` for the judgment of the targeted runs, which comes before the whole suite's: the suite
    runs on the candidate here only when the base suite named a module it could not load."""
    if not unloaded(run.base_suite, run.commands["suite"]):
        return []
    return reloaded(run, suite_on_candidate(run, trees, run_suite))


def flip(run, on_candidate) -> None:
    """What the whole suite on the candidate adds to the fail-to-pass proof: the repair the base suite's own
    failing tests show (existing_flip), or the tests a repair of unloadable code let run (``reloaded``)."""
    if run.existing_failures:
        failing = run.existing_failures
        if not changed_tests.inline_tests_kept(run.workspace, run.base, run.changes):
            # A Rust file lost or changed tests written beside its code: no module counts as repaired.
            failing = sorted(set(failing) - set(unloaded(run.base_suite, run.commands["suite"])))
        existing_flip(
            on_candidate,
            run.base_suite,
            failing,
            run.proof,
            run.fail,
            run.unverified,
            run.notes,
            run.allow_no_test,
        )
        tests, sources, _ = run.roles.partition(run.changes) if run.roles is not None else ([], [None], [])
        if tests and not sources:
            # A change of test support alone (autocode_test_support): its files must still define no test.
            test_support.check(tests, on_candidate, run.fail, run.notes, run.proof)
        return
    tests, proof = reloaded(run, on_candidate), run.proof
    if not tests or proof.get("fail_to_pass") is None:
        return
    # A test the targeted run already showed running and failing on the original code stays that.
    added = sorted(set(tests) - set(proof["fail_to_pass"]))
    if not added:
        return
    proof["not_run_on_base"] = sorted({*(proof.get("not_run_on_base") or []), *added})
    proof["fail_to_pass"] = sorted({*proof["fail_to_pass"], *added})
    run.notes.append(
        "The original code could not be loaded by its tests ("
        + ", ".join(unloaded(run.base_suite, run.commands["suite"])[:5])
        + "), and the change modifies none of them: the tests that could not run there and pass with the "
        "change: " + ", ".join(added[:20])
    )


def existing_flip(on_candidate, base_suite, failing, proof, fail, unverified, notes, allow_no_test):
    """The base suite's own failing tests are the regression tests of a repair that changed no test.

    They ran and failed on the original code (``failing``, repairable_failures); the ones that pass on
    the candidate are its fail-to-pass proof, the bug reproduced by a test the project already had, and
    the base's passing tests that still pass are the pass-to-pass set. When none passes now, the change
    fixed nothing those tests show, and the ordinary missing-test judgement stands.

    ``failing`` may also hold modules the base could not load (``unloaded``, when no test file changed):
    their tests that run and pass on the candidate (``loaded``) are fail-to-pass proof as well, and are
    listed as not run on the base.
    """
    candidate = on_candidate.get("results")
    if on_candidate["timed_out"] or not (candidate and candidate.get("complete")):
        unverified.append(
            "The project suite on the candidate reported no complete per-test results, "
            "so the existing failing tests cannot show the repair"
        )
        return
    base_results = base_suite["receipt"]["results"]
    gone = sorted(set(failing) & set(unloaded(base_suite, base_suite.get("command"))))
    new = loaded(base_results, candidate, gone) if gone else []
    ran = sorted(set(failing) & set(candidate["passed"]))
    flipped = sorted({*ran, *new})
    if not flipped:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, and no test that fails on the original code passes "
            "with the change, so the bug is not shown to be reproduced"
        )
        return
    proof.update(
        fail_to_pass=flipped,
        pass_to_pass=sorted(set(base_results["passed"]) & set(candidate["passed"])),
        not_run_on_base=new,
        failed_on_candidate=sorted(candidate["failed"]),
    )
    if ran:
        notes.append("Existing tests that fail on the original code pass with the change: " + ", ".join(ran[:20]))
    if new:
        notes.append(
            "No test changed, and the original code could not be loaded by its tests ("
            + ", ".join(gone[:5])
            + "): the tests the project already had run and pass with the change: "
            + ", ".join(new[:20])
        )


def repaired_by_flip(base_suite, on_base_with_tests, on_candidate):
    """The base's failing tests a repair that also changed tests is still shown to repair: they run and
    fail on the base with the new test files, and pass now.

    A change that edits test files loses the unchanged-test proof above, but the base suite's failing
    tests (``base_suite``) still run against the new test files (``on_base_with_tests``, the targeted
    regression run on the base) and their honest flip is the same repair evidence: the names that ran
    and failed there and pass on the candidate suite (existing_flip uses the same suite on the
    candidate). A name weakened to pass on the unfixed code never failed there and proves nothing; a
    name the targeted run did not re-run is unproven; collection and setup errors are not tests that
    ran; incomplete, missing or timed-out runs prove nothing either. New tests flipping are
    fail-to-pass evidence of new behavior (judge_regression), not the repair of a base failure.
    """
    if not on_base_with_tests or not on_candidate:
        return []
    if on_base_with_tests.get("timed_out") or on_candidate.get("timed_out"):
        return []
    base = on_base_with_tests.get("results") or {}
    candidate = on_candidate.get("results") or {}
    if not (base.get("complete") and candidate.get("complete")):
        return []
    base_results = ((base_suite or {}).get("receipt") or {}).get("results") or {}
    ran_and_failed = (
        set(base.get("failed") or [])
        - set(base.get("collection_errors") or [])
        - set((base.get("setup_errors") or {}).keys())
    )
    return sorted(set(base_results.get("failed") or []) & ran_and_failed & set(candidate.get("passed") or []))


# Runners that report only an exit code (Jest only for a command the proof cannot add its report to,
# autocode_jest_tests) and run the project's test files themselves, so what such a suite
# runs is decided by its test files and the manifests and runner configs the definition scan pins
# (autocode_verify._base_suite_definition). ``make test`` and a script package.json names run anything a
# recipe says, which that scan does not read, so they are left out. PHPUnit runs the tests its configuration
# file names and ``prove -lr t`` the ``.t`` files under ``t/`` with the options of a ``.proverc``; both
# files are runner configs of that scan. Maven and Gradle run what their build files say, which is the
# definition a JUnit proof of the same command pins (autocode_junit_proof.definition): the build files,
# the wrapper and the test sources stay the original ones, and a change outside product code, or to a
# product file that may define a test, leaves the boundary unestablished. verify() holds a repair of
# theirs to that definition (BUILD_TOOL_RUNNERS).
BUILD_TOOL_RUNNERS = frozenset({"maven", "gradle"})
EXIT_REPAIR_RUNNERS = frozenset({"jest", "mocha", "minitest", "phpunit", "prove"}) | BUILD_TOOL_RUNNERS


def exit_repair_suite(framework, suite, adopted) -> bool:
    """Whether ``suite`` can show a repair by its exit code alone (exit_flip): the suite of a detected runner
    of EXIT_REPAIR_RUNNERS, or, with no runner detected, the approved plan's command ``adopted``, which
    names test files the original code has (autocode_plan_proof_command.choose)."""
    if not suite:
        return False
    if framework is None:
        return suite == adopted
    return framework.name in EXIT_REPAIR_RUNNERS and suite == framework.suite


def repairable_exit(base_suite, suite_command) -> bool:
    """Whether the base suite, run with the proof's suite command, failed there as a whole, naming no test.

    The twin of repairable_failures for a run that reports only an exit code (a Mocha suite, a Jest script,
    Minitest files run one by one, a test script the plan names): its health is ``failing`` when the command
    ran to completion, exited nonzero and reported no per-test result (autocode_suite_health). A repair
    whose regression test the project already had changes no test, and a live Jest run of "this test
    fails, fix the code, do not change the tests" then failed its proof for want of a new test and asked the
    person to type a regression command (2026-11-13). exit_flip decides its proof.
    """
    suite = base_suite or {}
    return bool(suite_command) and suite.get("command") == suite_command and suite.get("health") == "failing"


def existing_repair(base_suite, suite_command, code_only, changed_tests=None):
    """What a repair that changed no test may rest on: (the base suite's failing tests, whether instead its
    failing exit code). The exit code counts only for a suite verify() was told can show it, and a change
    of code alone (``code_only``): another kind of file may be one the suite reads. With no test file in
    the change at all (``changed_tests`` is given and empty), the failing tests include the modules the base could not
    load (``unloaded``), which existing_flip judges by their tests on the candidate."""
    failing = repairable_failures(base_suite, suite_command)
    if changed_tests is not None and not changed_tests:
        failing = failing + unloaded(base_suite, suite_command)
    return failing, bool(not failing and code_only and repairable_exit(base_suite, suite_command))


def exit_flip(passes, base_suite, fail, unverified, notes, review_reasons, allow_no_test):
    """The base's failing suite is the regression test of a repair that changed no test, where none is named.

    It ran and failed on the original code (repairable_exit). ``passes`` says the same suite passes with the
    change: the candidate's run exited 0, and the suite's own files are the original ones or the original
    definition also passes over the new code (verify's base-definition run), so the change did not make
    the suite pass by running less of it. That is the bug reproduced by tests the project already had and
    fixed by the change; otherwise the ordinary missing-test judgement stands.
    """
    if not passes:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, and the suite that fails on the original code is not "
            "shown to pass with the change, so the bug is not shown to be reproduced"
        )
        return
    command, code = base_suite["command"], base_suite["receipt"]["exit_code"]
    notes.append(
        f"No test changed, and this project's runner names no test: its suite `{command}` fails on the original "
        f"code (exit {code}) and passes with the change, so the tests the project already has show the repair"
    )
    review_reasons.append("the repair rests on the whole suite's exit code on the original code and with the change")


def exit_repair(run, base_definition, completed) -> None:
    """Judge a repair that rests on the suite's exit code (``run.existing_exit``, verify's _Run), with exit_flip.

    The suite failed on the base as a whole; it shows the repair when it passes on the candidate with the
    suite's own files as the base had them: the definition's inputs did not change (no definition run), or
    the base's definition also passes over the candidate's code (``completed`` says a receipt is whole).
    verify's suite judgment reports that run itself.
    """
    if not run.existing_exit:
        return
    held = (base_definition or {}).get("run")
    passes = bool(
        base_definition is not None
        and not base_definition["boundary"]
        and (held is None or (completed(held) and not held["timed_out"] and held["exit_code"] == 0))
    )
    exit_flip(passes, run.base_suite, run.fail, run.unverified, run.notes, run.review_reasons, run.allow_no_test)


def repaired_evidence(proof, existing_failures, base_suite, checks, on_candidate, run=None):
    """The repair evidence for the preservation verdict: the proof's fail-to-pass flip when the base
    suite already proves the repair (``existing_failures``), else the flip the changed tests still
    show (``repaired_by_flip``, its targeted base run in ``checks["regression_on_base"]``).

    The modules the base could not load are repaired with their tests when those run and pass now
    (existing_flip and ``loaded``; for the proof ``run`` of a change that adds tests, ``reloaded``)."""
    if existing_failures:
        flipped = proof.get("fail_to_pass")
        if flipped and proof.get("not_run_on_base"):
            return [*flipped, *unloaded(base_suite, (base_suite or {}).get("command"))]
        return flipped
    flipped = repaired_by_flip(base_suite, checks.get("regression_on_base"), on_candidate)
    tests = reloaded(run, on_candidate) if run is not None else []
    return [*flipped, *tests, *unloaded(run.base_suite, run.commands["suite"])] if tests else flipped


def repaired_all(base_results, repaired, notes) -> bool:
    """Every failing base test was repaired (existing_flip or repaired_by_flip): a base with no passing
    test then has nothing lost."""
    if not repaired or not isinstance(base_results, dict) or not set(base_results["failed"]) <= set(repaired):
        return False
    if not base_results["passed"]:
        notes.append(
            "Every test of the original code failed there and passes with the change; nothing passing was lost"
        )
    return True


def suite_guards(on_candidate, base_suite, proof, *, unchanged, with_tests=None):
    """What the whole suite shows about guards (preserve cases), with complete per-test results on both runs.

    When no test changed (``unchanged``), nothing can flip and the base suite ran the same tests, so the
    targeted lists stay empty and the guards rest on its tests that pass on base and on the candidate, by
    id as well as by exact name (autocode_regression.check_cases). Otherwise
    ``with_tests`` is the suite on the base with the change's test files: its tests that pass there and on
    the candidate (``suite_pass_to_pass``) ran the same content on the original code and the change, which
    the pristine base cannot show once a test, fixture or golden file changed. ``failed_on_candidate`` is
    the candidate's failures: a guard with a failing variant does not hold, even when that variant already
    failed on base.
    """
    candidate = on_candidate.get("results")
    if not (candidate and candidate.get("complete")):
        return
    if unchanged:
        base = ((base_suite or {}).get("receipt") or {}).get("results")
        if base and base.get("complete"):
            proof.update(
                fail_to_pass=[],
                pass_to_pass=[],
                not_run_on_base=[],
                suite_pass_to_pass=sorted(set(base["passed"]) & set(candidate["passed"])),
                failed_on_candidate=sorted(candidate["failed"]),
            )
        return
    before = (with_tests or {}).get("results")
    if before and before.get("complete") and not with_tests.get("timed_out"):
        proof.update(
            suite_pass_to_pass=sorted(set(before["passed"]) & set(candidate["passed"])),
            failed_on_candidate=sorted(candidate["failed"]),
        )
    elif with_tests is not None:
        proof["guard_run_incomplete"] = True  # a guard left without a test is then unproven, not refuted


def passed_on(base_suite, base) -> set:
    """Tests that passed in a complete run of the pristine ``base`` suite (``verify.baseline``)."""
    if not base_suite or base_suite.get("base") != base:
        return set()
    receipt = base_suite.get("receipt") or {}
    results = receipt.get("results") or {}
    if receipt.get("timed_out") or not results.get("complete"):
        return set()
    return set(results.get("passed") or [])
