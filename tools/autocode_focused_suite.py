"""The bounded suite a regression proof falls back to when the whole detected suite cannot finish.

A detected suite is the whole project's. When its run on the original code times out, the proof
has nothing to compare the candidate with. AutoCode used to leave the proof UNVERIFIED there, and a
Resolver then asked a person for a longer timeout or a narrower test command: a question about its
own machinery (#3158). The proof now chooses the narrower command itself, deterministically, and
records the choice and its reason in its evidence.

The focused suite is the framework's own targeted command over the existing tests nearest the change:

* every changed test file the original code already holds, and
* every test file the original code holds that is named after a changed product file
  (``test_calc.py``, ``calc_test.go`` or ``calc.test.ts`` for ``calc``).

Nothing is chosen when no such test exists, when the framework cannot target them, or when the
targeted command is the whole suite again. An explicit ``--test-command`` is the person's choice
and the caller never replaces it. This module only selects and describes; it runs nothing.
"""

from __future__ import annotations

import copy
from pathlib import PurePosixPath

try:
    from . import autocode_python_test_roles as policy
except ImportError:
    import autocode_python_test_roles as policy

VERSION = 1
DECISION = "focused_suite"


def _named_after(product: str) -> frozenset[str]:
    """The test file names that name ``product`` as their subject."""
    path = PurePosixPath(product)
    stem, suffix = path.stem, path.suffix
    return frozenset(
        name + suffix
        for name in (
            f"test_{stem}",
            f"{stem}_test",
            f"{stem}_tests",
            f"{stem}.test",
            f"{stem}.spec",
            f"{stem}Test",
            f"{stem}Tests",
            f"Test{stem}",
        )
    )


def nearest_tests(base_files, changes, roles) -> list[str]:
    """The original code's test files nearest the change, sorted."""
    base_files = frozenset(base_files)
    tests = {
        path
        for path, status in changes.items()
        if status != "deleted" and path in base_files and roles.role(path) == policy.TEST
    }
    names = frozenset().union(
        *(
            _named_after(path)
            for path, status in changes.items()
            if status != "deleted" and roles.role(path) == policy.PRODUCT
        )
    )
    tests.update(path for path in base_files if PurePosixPath(path).name in names and roles.role(path) == policy.TEST)
    return sorted(tests)


def choose(framework, base_files, changes, roles) -> dict | None:
    """The focused suite for a whole detected suite that timed out, or None when there is none."""
    if framework is None:
        return None
    tests = nearest_tests(base_files, changes, roles)
    command = framework.targeted(roles.selectors(tests)) if tests else None
    if not command or command == framework.suite:
        return None
    return {"command": command, "tests": tests}


def timed_out(baseline) -> bool:
    """Whether a baseline result (autocode_verify.baseline) ended at its time limit."""
    return bool(baseline and (baseline.get("receipt") or {}).get("timed_out") is True)


def framework_for(framework, command):
    """A copy of ``framework`` whose whole suite is the focused ``command``; targeting is unchanged."""
    focused = copy.copy(framework)
    focused.suite = command
    return focused


def record(focus, whole, timeout) -> dict:
    """The evidence for the decision: what timed out, what replaced it, and why."""
    limit = f"{timeout} seconds" if timeout else "its time limit"
    return {
        "version": VERSION,
        "decision": DECISION,
        "whole_suite": whole["command"],
        "whole_suite_output": (whole.get("receipt") or {}).get("output"),
        "timeout_seconds": timeout,
        "command": focus["command"],
        "tests": list(focus["tests"]),
        "reason": (
            f"The detected whole suite did not finish on the original code within {limit}, so existing "
            "behavior was checked with the original tests nearest the change: " + ", ".join(focus["tests"])
        ),
    }


def review_reason(fallback) -> str:
    return (
        "the no-regression check ran only the original tests nearest the change, because the whole suite "
        "timed out on the original code: " + fallback["command"]
    )
