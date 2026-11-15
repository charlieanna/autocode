"""Where a change's tests run, found by running them (owner request, 2026-11-15).

AutoCode chose the directory and the suite for a changed test file from the repository's layout: a
manifest beside it, a ``requirements.txt``, what its code imports, whether the root holds a manifest.
Each layout the rules did not know failed the same way. The tests ran from a directory where they
cannot load, the proof said "the new tests only fail to import or collect", the Builder was sent back
and edited the project's tests to suit the proof, and the person was asked to correct the test
commands. Four fixes added four layouts (task-kind survey, sections 45 and 47 to 49); the owner asked
for one fix in their place.

No layout needs to be guessed. Every runner AutoCode reads reports a test file it could not load the
same way (``collection_errors``, ``uncollected``, or no test at all), in every language. So the
changed tests run once, on the changed code, where the layout rules put them. A file that does not
load there is run with the suite detected in each directory around it, innermost first, and then with
the root's: the first where it loads is its home. What is found is kept for the run (``test_homes`` in
its state), so later proofs do not look again, and the gate of the original tests runs a test whose
home is not the root with the same command (autocode_protected_oracles).

Choosing where a test runs proves nothing about the change. Wherever it runs, the proof still
requires that it fails on the original code and passes with the change, with the same command on
both, and the gate still puts the original tests back and runs them. A test that loads nowhere stays
where the rules put it and fails as before.

The same look serves every reader. The proof keeps what it found (``settle``, ``place``); the gate of
the original tests reads it (``away``, ``replay_commands``); a scratch run of given test files looks the
same way when they do not load (autocode_scratch_suites.run_tests); the plan check asks which directories
a planned test may run from (``places``); and a replay, which runs nothing, takes the recorded proof's
homes (``restore``).

It imports no controller and runs nothing itself: ``probe(suite, command, label)`` runs one command of
``suite`` on a copy of the changed code and returns its receipt, or None when it could not be run.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

try:
    from . import autocode_companion_suites as companion_suites
    from . import autocode_package_scope as package_scope
    from . import autocode_project_kinds as project_kinds
except ImportError:
    import autocode_companion_suites as companion_suites
    import autocode_package_scope as package_scope
    import autocode_project_kinds as project_kinds

if TYPE_CHECKING:
    from .autocode_run_state import RunState

VERSION = 1
ROOT = companion_suites.ROOT
# One settling runs at most this many commands; the tests left over stay where the rules put them.
MAX_PROBES = 12
# The longest one such command may run, in seconds.
PROBE_SECONDS = 300
_RAN = ("passed", "failed", "skipped")


def loads(receipt) -> bool | None:
    """Whether a run shows its tests load. True: tests ran and no file failed to load; a test that runs
    and fails has loaded. False: the runner says a file could not be loaded, ran no test at all, did not
    finish its report, or reports only an exit code and failed, which may be either. None: the run says
    nothing about it (it timed out or could not be started)."""
    if not isinstance(receipt, dict) or receipt.get("timed_out"):
        return None
    results = receipt.get("results")
    if not isinstance(results, dict):
        return None if receipt.get("error") else receipt.get("exit_code") == 0
    if results.get("collection_errors") or results.get("uncollected") or not any(results.get(key) for key in _RAN):
        return False
    return bool(results.get("complete"))


def places(test) -> list[str]:
    """The directories below the root that ``test`` may run from: those around it, innermost first. A test
    directory is no place to run its own tests from (``backend/tests/``), nor is a fixture among the tests."""
    parts = PurePosixPath(test).parent.parts
    around = ["/".join(parts[:end]) for end in range(len(parts), 0, -1)]
    return [project for project in around if not project_kinds.is_test_path(project + "/_")]


def candidates(test, root_framework, suite_of) -> list[tuple[str, Any]]:
    """(project, suite) for each place ``test`` may run from: its ``places``, then the root, each with a
    detected suite (``suite_of(project)``) that can be given the file."""
    found = []
    for project in places(test):
        suite = suite_of(project)
        if suite is not None and suite.targeted([test]):
            found.append((project, suite))
    if root_framework is not None and root_framework.targeted([test]):
        found.append((ROOT, root_framework))
    return found


def _saved(state: RunState) -> dict:
    """The homes saved in the run's state (``test_homes``): file -> home."""
    saved = state.get("test_homes")
    files = saved.get("files") if isinstance(saved, dict) and saved.get("version") == VERSION else None
    return files if isinstance(files, dict) else {}


def _static(chosen, tests) -> dict[str, tuple[str, Any]]:
    """Where the layout rules put each test: (project, the suite that runs it there or None)."""
    framework, suites, _notes = chosen
    placed = {file: (suite["project"], suite["framework"]) for suite in suites for file in suite["files"]}
    own = getattr(framework, "directory", None) or ROOT
    result = {}
    for test in tests:
        project, suite = placed.get(test, (own, framework))
        result[test] = (project, suite if suite is not None and suite.targeted([test]) else None)
    return result


def known(state: RunState, static) -> dict[str, dict]:
    """The saved home of each test the rules still put where they did when it was found: a dict of
    ``project``, ``from`` (where the rules put it), ``moved``, the ``command`` that runs it alone there and
    the ``suite`` of that project. A test the rules now put elsewhere is looked for again."""
    files = _saved(state)
    return {
        test: files[test]
        for test, (project, _suite) in static.items()
        if isinstance(files.get(test), dict) and files[test].get("from") == project
    }


def settle(state: RunState, root, root_framework, chosen, tests, detect_in, probe) -> None:
    """Find the home of each of ``tests`` not settled yet and save it.

    ``chosen`` is what the layout rules chose (autocode_companion_suites.test_suites). Each of its
    groups runs once: tests that load stay. A test that does not load there, or that no suite there
    runs, goes to the first of its ``candidates`` where it loads; the tests whose next place is the same
    are tried there together first. One that ran among others may still load where the rules put it,
    alone. A group whose run timed out stays where the rules put it and is not run again to look: such
    a run costs its whole time limit and shows nothing. A test that loads nowhere, or whose run could not
    be started, is looked for again by the next proof.
    """
    static = _static(chosen, sorted(tests))
    settled = known(state, static)
    static = {test: where for test, where in static.items() if test not in settled}
    left = [MAX_PROBES]
    detected: dict[str, Any] = {}

    def suite_of(project):
        if project not in detected:
            detected[project] = package_scope.detect(root, project, detect_in)
        return detected[project]

    def run(suite, files, label):
        if left[0] <= 0:
            return None
        left[0] -= 1
        return probe(suite, suite.targeted(files), label)

    homes: dict[str, dict] = {}
    groups: dict[str, tuple[Any, list[str]]] = {}
    lost = [test for test, (_project, suite) in static.items() if suite is None]
    for test, (project, suite) in static.items():
        if suite is not None:
            groups.setdefault(project, (suite, []))[1].append(test)
    alone: set[str] = set()  # tests that did not load where the rules put them, each in a run of its own
    for project, (suite, files) in sorted(groups.items()):
        receipt = run(suite, files, "rules")
        seen = loads(receipt)
        if seen is False:
            lost += files
            alone.update(files if len(files) == 1 else ())
        elif seen or isinstance(receipt, dict) and receipt.get("timed_out"):
            homes.update({file: _home(project, project, suite, file) for file in files})
    waiting = {test: candidates(test, root_framework, suite_of) for test in sorted(lost)}
    for test in alone:  # that place is not tried again; one that ran among others may load there alone
        waiting[test] = [(project, suite) for project, suite in waiting[test] if project != static[test][0]]
    while any(waiting.values()):
        first = next(test for test in sorted(waiting) if waiting[test])
        project, suite = waiting[first][0]
        together = [test for test in sorted(waiting) if waiting[test] and waiting[test][0][0] == project]
        for test in together:
            waiting[test] = waiting[test][1:]
        # Together first: one run settles them all. One that still does not load is then run alone.
        for files in [together] if len(together) == 1 else [together, *([test] for test in together)]:
            files = [file for file in files if file not in homes]
            if files and loads(run(suite, files, project or "root")):
                for file in files:
                    homes[file] = _home(project, static[file][0], suite, file)
                    waiting[file] = []
    state["test_homes"] = {"version": VERSION, "files": {**_saved(state), **homes}}


def _home(project, before, suite, file) -> dict:
    return {
        "project": project,
        "from": before,
        "moved": project != before,
        "command": suite.targeted([file]),
        "suite": suite.suite,
    }


def unsettled(state: RunState, chosen, tests) -> bool:
    """Whether ``settle`` has a test to look for."""
    static = _static(chosen, sorted(tests))
    return len(known(state, static)) < len(static)


def place(state: RunState, root, root_framework, chosen, tests, detect_in) -> tuple:
    """(framework, suites, notes): ``chosen`` with each moved test in the suite of its saved home.

    The proof's own suite stays the one that runs the tests left with it; when it runs none of the
    changed tests and another project does, the first such project's suite is the proof's, as when the
    rules choose (autocode_companion_suites.test_suites).
    """
    framework, suites, notes = chosen
    static = _static(chosen, sorted(tests))
    moved = {test: home for test, home in known(state, static).items() if home.get("moved")}
    if not moved:
        return chosen
    found = []
    for test, home in sorted(moved.items()):
        before, suite = static[test]
        found.append(
            f"The changed test {test} "
            + (
                f"does not load from {companion_suites.where(before)} (`{suite.targeted([test])}`)"
                if suite is not None
                else f"is run by no suite of {companion_suites.where(before)}"
            )
            + f"; it loads from {companion_suites.where(home['project'])} (`{home['command']}`), "
            "so that is where it runs"
        )
    own = getattr(framework, "directory", None) or ROOT
    files: dict[str, list[str]] = {suite["project"]: list(suite["files"]) for suite in suites}
    frameworks: dict[str, Any] = {suite["project"]: suite["framework"] for suite in suites}
    held = [test for test in sorted(tests) if not any(test in group for group in files.values())]
    for test, home in sorted(moved.items()):
        project = home["project"]
        if project not in frameworks:
            frameworks[project] = root_framework if project == ROOT else package_scope.detect(root, project, detect_in)
        if frameworks[project] is None or not frameworks[project].targeted([test]):
            continue  # its home no longer runs it: the rules' choice stands, and the proof says what fails
        held = [name for name in held if name != test]
        for group in files.values():
            if test in group:
                group.remove(test)
        files.setdefault(project, []).append(test)
    if own in files:
        held += files.pop(own)
    files = {project: sorted(group) for project, group in files.items() if group}
    if files and not (framework is not None and any(framework.targeted([test]) for test in held)):
        first = sorted(files)[0]
        framework = frameworks[first]
        del files[first]
    said = {companion_suites.proves(chosen[0], suite) for suite in suites}
    kept = [note for note in notes if note not in said and not any(test in note for test in moved)]
    suites = [
        {"project": project, "framework": frameworks[project], "files": group}
        for project, group in sorted(files.items())
    ]
    return framework, suites, [*kept, *found, *(companion_suites.proves(framework, suite) for suite in suites)]


def _whole(files) -> dict[str, dict]:
    """The homes among ``files`` that are whole records."""
    return {
        name: home
        for name, home in (files.items() if isinstance(files, dict) else ())
        if isinstance(name, str)
        and isinstance(home, dict)
        and isinstance(home.get("moved"), bool)
        and all(isinstance(home.get(key), str) for key in ("project", "from", "command", "suite"))
    }


def _moved(files) -> dict[str, dict]:
    return {name: home for name, home in _whole(files).items() if home["moved"]}


def moved(state: RunState) -> dict[str, dict]:
    """The saved home of every test that was moved: ``project``, the ``command`` that runs the file alone
    there and the ``suite`` of that project."""
    return _moved(_saved(state))


def away(state: RunState) -> dict[str, dict]:
    """The saved home of every test that does not run where the rules' first choice, the root, runs it: one
    a run moved, and one the rules put in a project below the root and a run saw load there. The gate of
    the original tests runs these with the command the proof used, so the two cannot come to disagree."""
    return {name: home for name, home in _whole(_saved(state)).items() if home["moved"] or home["project"] != ROOT}


def restore(state: RunState, recorded) -> None:
    """Take the homes a recorded proof kept (its ``test_homes``) as this run's. A replay runs no command, so it
    cannot look for itself; with them it derives the commands the recording ran, which it then compares."""
    state["test_homes"] = {"version": VERSION, "files": {**_saved(state), **_moved(recorded)}}


def replay_commands(homes, names, intact) -> dict[str, str]:
    """The command that runs each of the changed original files ``names`` in the home the proof found
    (``homes``: ``away``), for the gate of the original tests: such a test's own command, and its project's
    whole suite for a file that is no such test but lies in such a project (a helper or a fixture of its
    tests), or that is not ``intact(name)`` (deleted, or replaced by a link). A project whose whole suite
    runs needs no command for one of its files. Names with no such home are left out: the bound command
    runs them."""
    suites = {home["project"]: home["suite"] for home in homes.values() if home["project"] != ROOT}
    found: dict[str, str] = {}
    for name in names:
        home = homes.get(name)
        if home is not None and intact(name):
            found[name] = home["command"]
            continue
        around = [project for project in suites if package_scope.contains(project, name)]
        if around:
            found[name] = suites[max(around, key=len)]
    whole = {suite for suite in found.values() if suite in suites.values()}
    return {
        name: suites[homes[name]["project"]]
        if name in homes and suites.get(homes[name]["project"]) in whole
        else command
        for name, command in found.items()
    }
