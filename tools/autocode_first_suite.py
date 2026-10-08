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


def absent_base_suite(base_receipt, candidate_receipt, *, document_only):
    """A new suite may not exist on base; the candidate must actually run it.

    The caller establishes document_only from the pinned Git inventory, matching
    baseline identity and command, and new-behavior mode without a base patch.
    This is evidence of no old behavior to preserve, not a successful base run.
    It never substitutes for the separate new-behavior proof.
    """
    return bool(document_only
                and base_receipt.get("results_expected") is True
                and base_receipt.get("results") is None
                and base_receipt.get("exit_code") == 1
                and base_receipt.get("timed_out") is False
                and _candidate_passed(candidate_receipt))


def _candidate_passed(candidate_receipt):
    candidate = candidate_receipt.get("results") or {}
    return bool(candidate_receipt.get("timed_out") is False
                and candidate_receipt.get("exit_code") == 0
                and schedule.complete_results(candidate_receipt)
                and candidate.get("passed") and not candidate.get("failed")
                and not candidate.get("skipped"))


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
    return bool(no_go_project
                and go_reported_nothing(base_receipt)
                and base_receipt.get("results_expected") is True
                and base_receipt.get("exit_code") == 1
                and base_receipt.get("timed_out") is False
                and _candidate_passed(candidate_receipt))
