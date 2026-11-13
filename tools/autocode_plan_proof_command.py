"""The approved plan's own test command, for a regression proof that found no runner it knows.

The proof takes its commands from three places (autocode_verification_commands.select_commands): a
command the person typed, a suite AutoCode detects from the repository, or the Builder's report. The
Builder's is never trusted, because the candidate would be judged by a check it chose. Detection is a
list of runners per language (autocode_toolchain_suite.detect), so every language or layout it did
not list left the proof with no trusted command at all. The proof then said "pass --test-command",
and the Resolver asked the person to type it: a Ruby Minitest bug fix did exactly that on Claude
models (2026-11-13, docs/bugs/2026-11-13-task-kind-survey.md), although the approved plan already
said `ruby test/pager_test.rb` runs the tests before and after the fix.

A command written in the approved plan is fixed before any Builder runs and was reviewed with the
plan, so the candidate is not judged by a check it chose. When nothing is typed and nothing is
detected, ``choose`` takes the first approved command that runs a test file, deterministically:

- as the suite when every test file it names exists in the original code, so it can pass there;
- as the regression command when it names a test file the change adds or edits.

The proof still runs it on the original code and with the change and judges its exit code, and the
caller records the choice in the proof's notes and review reasons. Pure: callers pass the plan body
and the test paths.
"""

from __future__ import annotations

import shlex

try:
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_verification_plan as verification_plan


def approved_commands(contract) -> list[str]:
    """The commands the approved plan's acceptance criteria run, in criterion order; [] unless approved."""
    if not isinstance(contract, dict) or contract.get("approval_status") != "approved":
        return []
    rows = (contract.get("body") or {}).get("acceptance_criteria") or []
    found = [
        command
        for row in rows
        if isinstance(row, dict) and not row.get("human_review")
        for command in verification_plan.commands(row.get("verification_method") or "")
    ]
    return list(dict.fromkeys(found))


def _named(command, paths) -> set[str]:
    """The ``paths`` a shell word of ``command`` names, as a path or ./path."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        words = command.split()
    named = {word[2:] if word.startswith("./") else word for word in words}
    return {path for path in paths if path in named}


def choose(commands, *, original_tests, changed_tests) -> dict | None:
    """The first of ``commands`` that runs a test file, with the roles it can take, or None.

    ``original_tests`` are the test files of the original code, ``changed_tests`` those the change adds or
    edits (not deleted). The result's ``suite`` is the command when every test file it names is an original
    one, ``regression`` when it names a changed one; a command that can take neither is skipped.
    """
    original, changed = set(original_tests), set(changed_tests)
    for command in commands:
        named = _named(command, original | changed)
        suite = bool(named) and named <= original
        regression = bool(named & changed)
        if suite or regression:
            return {
                "command": command,
                "suite": command if suite else None,
                "regression": command if regression else None,
            }
    return None


def note(chosen) -> str:
    """What the proof records about the choice, for the report and the Completion Reviewer."""
    roles = " and ".join(
        role for role, key in (("the suite", "suite"), ("the regression command", "regression")) if chosen.get(key)
    )
    return (
        f"AutoCode found no test runner it reads in this project, so it ran the approved plan's command "
        f"`{chosen['command']}` as {roles}; the proof rests on that command's exit code"
    )
