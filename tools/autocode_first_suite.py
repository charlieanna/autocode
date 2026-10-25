"""Preservation evidence when a pinned base has no behavior for the suite being proved.

A documentation-only tree, or a tree with no Go project when the suite is Go.
"""

from pathlib import PurePosixPath

try:
    from . import autocode_verification_schedule as schedule
except ImportError:
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


def inert_base_file(path, size, *, separate, source) -> bool:
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
