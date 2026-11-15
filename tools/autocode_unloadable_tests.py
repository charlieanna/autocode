"""The test files the original code cannot load, and the suite without them.

A project may hold a test file that cannot be loaded before the task begins: a spec that requires a
repository kept beside the project, a module whose dependency is not installed, a file left broken.
The change did not cause that and often cannot repair it. Every runner AutoCode reads names such a
file in its report (``uncollected``), and the proof then called the whole comparison unproven:

* RSpec runs no example at all when one spec file cannot be loaded, so nothing was compared. The
  tests nearest the change stood in when there were any (autocode_focused_suite); a change that only
  adds files has none, its proof ended UNVERIFIED, and a Tester that had passed every criterion
  failed the run for the proof alone (2026-11-15).
* unittest and Node run the other files, and those tests passed before and after the change. The
  proof was UNVERIFIED all the same, for the file that could not load.

One cause, so one answer, found by running: the whole suite on the original code names the files it
could not load there. When the change touches none of them, and running them on the changed code
shows that none loads there either, the proof runs the suite without them on the original code and
with the change, and names them in its evidence (``suite_fallback``) and to the reviewers. The suite
without them must load completely on the original code and pass every test the whole suite passed
there, so leaving a file out never drops a test that ran.

Leaving a file out decides nothing about what passes. The command that runs is judged by the same
rules as any other suite: a file it cannot load, a test that newly fails or a missing result still
fails or unproves the proof. A file the change repairs is not left out; the tests that could not run
before prove that repair (autocode_first_suite.loaded). A person's ``--test-command`` is never
replaced. A runner that names what it could not build by package or target (Go, Cargo) names no
file, and stays as it was.

This module only selects and describes; it runs nothing and imports no controller.
"""

from __future__ import annotations

from pathlib import PurePosixPath

VERSION = 1
DECISION = "loadable_suite"
# What the whole suite did on the original code, beside autocode_focused_suite's outcomes.
UNLOADABLE = "unloadable_tests"
# A command that names every other test file of a large suite is no command a person or a reviewer
# can read, and it is repeated in every receipt of the proof. Such a suite stays as it was.
MAX_COMMAND = 4000
_SHOWN = 10
# The ids a result parser gives a file that did not load: ``FILE::[collection]`` (RSpec, Node, Jest,
# Vitest, Minitest), ``::pkg.module`` (pytest) and unittest's own failed-import tests, which name a module.
_FILE_MARK = "::[collection]"
_MODULE_MARKS = ("unittest.loader._FailedTest.", "unittest.loader.ModuleImportFailure.", "::")


def _results(run):
    """The per-test results of a suite run: autocode_verify.baseline's record, or a bare receipt."""
    run = run if isinstance(run, dict) else {}
    receipt = run["receipt"] if isinstance(run.get("receipt"), dict) else run
    results = receipt.get("results")
    return results if isinstance(results, dict) else None


def unloaded(run) -> list[str]:
    """The ids a suite run gives the files it could not load."""
    results = _results(run) or {}
    return sorted(results.get("uncollected", results.get("collection_errors")) or [])


def _within(directory, path) -> str | None:
    """``path`` as the suite's runner names it: relative to the directory its command enters."""
    if not directory:
        return path
    return path[len(directory) + 1 :] if path.startswith(directory + "/") else None


def _names(identity, name) -> bool:
    """Whether a not-loaded id names the test file the runner knows as ``name``."""
    if identity.endswith(_FILE_MARK):
        return identity[: -len(_FILE_MARK)].removeprefix("./") == name
    for mark in _MODULE_MARKS:
        if identity.startswith(mark) and "::" not in identity[len(mark) :]:
            module = identity[len(mark) :]
            dotted = ".".join(PurePosixPath(name).with_suffix("").parts)
            # unittest names a module it was given as a file by its last part alone.
            return bool(module) and (dotted == module or dotted.endswith("." + module))
    return False


def _ran(identities, name) -> bool:
    """Whether one of a run's test ids is a test of the file the runner knows as ``name``: ``FILE::test``,
    ``pkg.module::test`` (pytest) or ``pkg.module.Class.test`` (unittest)."""
    dotted = ".".join(PurePosixPath(name).with_suffix("").parts)
    starts = (name + "::", "./" + name + "::", dotted + "::", dotted + ".")
    return any(identity.startswith(starts) for identity in identities)


def files(identities, tests, directory=None) -> list[str] | None:
    """The files among ``tests`` (repository paths) that ``identities`` name, sorted; None when an id
    names no file or more than one, so nothing is left out that cannot be named exactly."""
    named = []
    for identity in identities:
        matches = [path for path in tests if (name := _within(directory, path)) and _names(identity, name)]
        if len(matches) != 1:
            return None
        named.append(matches[0])
    return sorted(set(named))


def choose(framework, whole, tests, changes) -> dict | None:
    """The suite without the test files ``whole`` (the whole suite on the original code) could not load,
    or None when there is none to choose: it loaded everything, an id names no single original test file,
    the change touches one of them, or the runner has no command that leaves them out.

    ``tests`` are the original code's test files and ``changes`` the changed paths. The result names the
    command, the files left out and the command that runs those files alone (``probe``), which the caller
    runs on the changed code before it uses the choice (``still``).
    """
    identities = unloaded(whole)
    if framework is None or not identities:
        return None
    directory = getattr(framework, "directory", None)
    left_out = files(identities, tests, directory)
    if not left_out or any(path in changes for path in left_out):
        return None
    # The files the whole suite ran, where it ran any: a file it never runs is not added by naming it.
    results = _results(whole) or {}
    ran = [identity for key in ("passed", "failed", "skipped") for identity in results.get(key) or ()]
    ran = sorted(set(ran) - set(identities))
    remaining = [
        path
        for path in tests
        if path not in left_out and (not ran or ((name := _within(directory, path)) and _ran(ran, name)))
    ]
    command = framework.without(left_out, remaining)
    probe = framework.targeted(left_out)
    if not command or not probe or command == framework.suite or len(command) > MAX_COMMAND:
        return None
    return {"command": command, "left_out": left_out, "probe": probe, "directory": directory}


def still(receipt, choice) -> bool:
    """Whether a run of the left-out files on the changed code shows that every one of them still does
    not load, and that no test of theirs ran. A file that loads now is the change's own repair."""
    results = _results(receipt)
    if results is None or receipt.get("timed_out"):
        return False
    identities = unloaded(receipt)
    ran = {*(results.get("passed") or ()), *(results.get("failed") or ()), *(results.get("skipped") or ())}
    if ran - set(identities):
        return False
    return files(identities, choice["left_out"], choice.get("directory")) == choice["left_out"]


def keeps(whole, without) -> bool:
    """Whether the suite without those files, on the original code, loads every file it runs and passes
    every test the whole suite passed there. Leaving a file out must not lose a test that ran."""
    results, before = _results(without), _results(whole) or {}
    if results is None or not results.get("complete") or unloaded(without):
        return False
    passed = set(results.get("passed") or ())
    return bool(passed) and set(before.get("passed") or ()) <= passed


def _listed(paths) -> str:
    more = len(paths) - _SHOWN
    return ", ".join(paths[:_SHOWN]) + (f" and {more} more" if more > 0 else "")


def record(choice, whole) -> dict:
    """The evidence for the decision: what the whole suite could not load, and what ran in its place."""
    left_out = list(choice["left_out"])
    one = len(left_out) == 1
    untouched = (
        "The change does not touch it and it does not load with the change either"
        if one
        else "The change touches none of them and none loads with the change either"
    )
    return {
        "version": VERSION,
        "decision": DECISION,
        "whole_suite": whole["command"],
        "whole_suite_output": (whole.get("receipt") or {}).get("output"),
        "whole_suite_outcome": UNLOADABLE,
        "command": choice["command"],
        "left_out": left_out,
        "reason": (
            f"The detected whole suite could not load {len(left_out)} test file{'' if one else 's'} on the "
            f"original code. {untouched}, so existing behavior was checked with the suite without "
            f"{'it' if one else 'them'}: " + _listed(left_out)
        ),
    }


def review_reason(fallback) -> str:
    left_out = list(fallback.get("left_out") or [])
    return (
        f"the no-regression check left out {len(left_out)} test file{'s' if len(left_out) != 1 else ''} that "
        "cannot be loaded on the original code or with the change, so no test of theirs ran: " + _listed(left_out)
    )
