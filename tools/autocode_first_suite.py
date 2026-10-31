"""Preservation evidence when a pinned base has no behavior for the suite being proved.

A documentation-only tree, or a tree with no Go project when the suite is Go.
"""

import subprocess
from pathlib import PurePosixPath

try:
    from . import autocode_project_inventory as project_inventory
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_project_inventory as project_inventory
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
        "pyproject.toml",
        "setup.cfg",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "pdm.lock",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "go.mod",
        "go.sum",
        "Cargo.toml",
        "Cargo.lock",
        "pom.xml",
        "gradle.properties",
        "Gemfile.lock",
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
        shown = subprocess.run(["git", "cat-file", "blob", f"{revision}:{path}"], cwd=workspace, capture_output=True)
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
    supplied by ``read``) holds no behavior either (#2259).
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


def existing_flip(on_candidate, base_suite, failing, proof, fail, unverified, notes, allow_no_test):
    """The base suite's own failing tests are the regression tests of a repair that changed no test.

    They ran and failed on the original code (``failing``, repairable_failures); the ones that pass on
    the candidate are its fail-to-pass proof, the bug reproduced by a test the project already had, and
    the base's passing tests that still pass are the pass-to-pass set. When none passes now, the change
    fixed nothing those tests show, and the ordinary missing-test judgement stands.
    """
    candidate = on_candidate.get("results")
    if on_candidate["timed_out"] or not (candidate and candidate.get("complete")):
        unverified.append(
            "The project suite on the candidate reported no complete per-test results, "
            "so the existing failing tests cannot show the repair"
        )
        return
    flipped = sorted(set(failing) & set(candidate["passed"]))
    if not flipped:
        (unverified if allow_no_test else fail).append(
            "No regression test was added or changed, and no test that fails on the original code passes "
            "with the change, so the bug is not shown to be reproduced"
        )
        return
    base_results = base_suite["receipt"]["results"]
    proof.update(
        fail_to_pass=flipped,
        pass_to_pass=sorted(set(base_results["passed"]) & set(candidate["passed"])),
        not_run_on_base=[],
        failed_on_candidate=sorted(candidate["failed"]),
    )
    notes.append("Existing tests that fail on the original code pass with the change: " + ", ".join(flipped[:20]))


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


def repaired_evidence(proof, existing_failures, base_suite, checks, on_candidate):
    """The repair evidence for the preservation verdict: the proof's fail-to-pass flip when the base
    suite already proves the repair (``existing_failures``), else the flip the changed tests still
    show (``repaired_by_flip``, its targeted base run in ``checks["regression_on_base"]``)."""
    if existing_failures:
        return proof.get("fail_to_pass")
    return repaired_by_flip(base_suite, checks.get("regression_on_base"), on_candidate)


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
