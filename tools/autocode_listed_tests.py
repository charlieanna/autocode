"""A change's new tests that run only because another file of the change lists them.

The regression proof needs the change's tests to fail on the original code, so it runs them on a tree of the
original code with the change's test files. What a test file is, is a rule over paths. A project whose
build file lists each test (a ``Makefile`` target per test program, an ``npm test`` script of
``node test/a.js && node test/b.js``, a ``run_tests.sh`` that names each file) runs a new test only once
that file is edited, and the build file is no test file. On the original code with the test files alone the
project's test command never ran the new test, passed, and the proof said "The new tests pass on the
unfixed base code, so they do not reproduce the bug" of a test that had not run. No work on the tests
settles that: a real feature in a C project whose ``Makefile`` lists each test program (2026-11-15) failed
its proof twice and ended asking the person to set a regression command (12 stages).

Which file lists a test is not a rule either: it is seen by running. When the proof's command passes on the
original code with the change's test files, by its exit code alone, AutoCode runs it again:

- on the original code with the change's test files made unloadable: the ones it passes without are not
  run there (with several test files and a failure, each is tried alone). A test that is run there has
  shown what it shows, and nothing more is looked at for it;
- on the change with those test files unloadable: a command that still passes runs none of them, with the
  change or without, and the Builder is told so;
- on the change with one other changed file put back as the original code has it, those test files still
  unloadable: a command that passes now no longer runs them, and nothing else broke, so that file is what
  lists them.

The proof then runs again with those files taken with the change's tests, in both places the proof keeps
tests apart from the code. The original code the tests are run on holds them too. And the check that the
original tests still pass with the change (the base suite definition, #587) keeps them as the original code
has them: before, that check ran the change's listing without the change's tests, so once the person had
set the regression command the first failure asked for, the same change failed again with "The base suite
definition fails against the candidate code". That failure, alone, is the second reason to look.

Nothing about what passes changes: the command must still fail on the original code and pass with the
change, the original tests must still pass with the change, and a file that carries the fix itself makes
the command pass on the original code, as before. What was seen is recorded in the proof (``listed_tests``).
At most MAX_FILES other files are looked at, files that are no code first, one run each of at most
RUN_SECONDS; a change with one test file that the command runs on the original code costs one run.

Pure functions: the caller makes the trees and runs the command. Imports nothing from AutoCode.
"""

from __future__ import annotations

# The other changed files one proof puts back, one at a time, to see whether each lists the tests.
MAX_FILES = 6
# The longest one such run may take, in seconds.
RUN_SECONDS = 300
# What a test file is overwritten with for these looks: no language loads it.
UNLOADABLE = ")]}<<<<<<< AutoCode made this test file unloadable to see which file lists it\n\x00\n"
# How the proof says its command passed on the original code with the change's tests, by exit code.
_PASSED_ON_BASE = "on the unfixed base code, so they do not reproduce the bug"
# How it says the original tests, run by the original code's own listing, no longer pass with the change.
_DEFINITION_FAILED = "The base suite definition fails against the candidate code"
# Why a proof looked (``wanted``).
PASSED, DEFINITION = "passed_on_base", "base_definition_failed"


def _passed(receipt) -> bool:
    return bool(receipt) and receipt.get("exit_code") == 0 and not receipt.get("timed_out")


def wanted(result) -> tuple[str, str] | None:
    """(command, reason): the command to look with, when a proof failed for one reason alone and that
    reason is one a file that lists the change's tests explains. Both are judged by exit code (a runner
    that names its tests says which test ran):

    - ``PASSED``: its command passed on the original code with the change's tests, which may never have
      run there;
    - ``DEFINITION``: the original code's suite, with its own listing put back and the change's tests left
      out, fails with the change, whose listing may name a test that tree does not hold."""
    failures = [str(failure) for failure in result.get("failures") or []]
    if result.get("verdict") != "FAIL" or len(failures) != 1:
        return None
    checks, commands = result.get("checks") or {}, result.get("commands") or {}
    if failures[0].endswith(_PASSED_ON_BASE):
        on_base = checks.get("regression_on_base") or {}
        command = on_base.get("command") or commands.get("regression") or commands.get("suite")
        return (command, PASSED) if command and _passed(on_base) and on_base.get("results") is None else None
    if failures[0].startswith(_DEFINITION_FAILED):
        definition = checks.get("suite_base_definition_on_candidate") or {}
        command = definition.get("command") or commands.get("suite")
        return (command, DEFINITION) if command and definition.get("results") is None else None
    return None


def find(tests, others, look) -> tuple[list[str], list[str], dict]:
    """(files, unrun, seen): the ``others`` (changed files that are no test, in the order to look at them)
    that list tests of the change, and the ``tests`` (changed test files) the command does not run on the
    original code, which are the ones a file can list.

    ``look(reverted, unloadable)`` runs the proof's command on the change with the paths of ``reverted``
    put back as the original code has them and those of ``unloadable`` made unloadable, and returns its
    exit code, or None when the run did not finish. ``seen`` holds each exit code, for the proof."""
    seen: dict = {}
    tests, others = list(tests), list(others)
    if not tests or not others:
        return [], [], seen
    # On the original code with the change's tests: which of them does the command not run there?
    seen["original_without_tests"] = look(tuple(others), tuple(tests))
    if seen["original_without_tests"] == 0:
        unrun = tests
    elif seen["original_without_tests"] is None or len(tests) == 1 or len(tests) > MAX_FILES:
        return [], [], seen  # the one test runs there, or nothing was seen
    else:
        unrun = []
        for path in tests:
            seen[f"original_without:{path}"] = look(tuple(others), (path,))
            if seen[f"original_without:{path}"] == 0:
                unrun.append(path)
        if not unrun:
            return [], [], seen
    seen["change_without_tests"] = look((), tuple(unrun))
    if seen["change_without_tests"] in (0, None):
        return [], unrun, seen  # the command runs none of them even with the change, or did not finish
    found = []
    for path in others[:MAX_FILES]:
        code = look((path,), tuple(unrun))
        seen[f"without:{path}"] = code
        if code == 0:
            found.append(path)
    return found, unrun, seen


def unrun(seen) -> bool:
    """Whether the looks showed that the command runs none of the change's tests, even with the change."""
    return seen.get("change_without_tests") == 0


def note(command, tests, files) -> str:
    """What the proof records when files of the change list its tests."""
    listed, one = ", ".join(sorted(files)), len(files) == 1
    return (
        f"{listed}, which the change edits, {'lists' if one else 'list'} the change's tests "
        f"{', '.join(sorted(tests))}: with the change, `{command}` fails when they cannot be loaded, and passes "
        f"when {'that file is' if one else 'one of those files is'} put back as it was. So {listed} "
        f"{'is' if one else 'are'} taken with the tests: the original code they were run on holds "
        f"{'it' if one else 'them'}, and the original tests were run with the change as the original code "
        f"lists them"
    )


def unrun_note(command, tests) -> str:
    """What the proof tells the Builder when its command runs none of the change's tests."""
    return (
        f"`{command}` passes with the change even when {', '.join(sorted(tests))} cannot be loaded: it does "
        "not run them, so they show nothing. Add them where this project's test command runs its tests"
    )
