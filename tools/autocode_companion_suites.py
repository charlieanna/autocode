"""The other project suites a change reaches, run as its pass-to-pass protection (#2889).

A repository can hold projects with suites of their own: a Node suite at the root and a Rails app with
its RSpec specs under backend/. The regression proof runs one suite. A change to backend/ code proven
with the root's Node suite, or a change to the root's code proven with backend/'s suite, was never run
against the other project's own tests, so "no test that passed before now fails" was checked against a
suite that held none of them (a live idlecampus fix broke 9 backend examples, 2026-11-09).

``find`` names, for the changed code files, each project around them (the innermost directory with its
own manifest, as autocode_package_scope.MANIFESTS lists them, or the repository root) whose tests the
proof's suite cannot run, with that project's own detected suite. autocode_regression runs each on the
original code and with the change, and ``judge`` compares the two runs: a test that passed and does not
pass with the change, or a suite that passed and fails with it, fails the proof; a run that did not
finish, or an exit-code-only suite that already fails on the original code, cannot show it, and the
proof is unverified. Each decision is a note in the proof's evidence, never a question for a person.

A plan's guard may name a test of another project: a live Kilo run (2026-11-11) proved its fix with
backend/'s suite while a guard named a root Node test. The proof looked for it only in its own suite, so the
guard failed twice and the Builder was sent back for a test that passed all along. A project's whole suite
may report no per-test results either (`npm test`). ``guard_suites`` names, for each other project whose
test files name a guard's test and whose tests the proof's suite cannot run, the command that runs just
those files with that project's own suite; autocode_regression runs it on the original code and with the
change, and ``guard_results`` gives the tests a guard may rest on: those that passed in both complete runs.

Pure helpers over paths, frameworks and receipts: nothing here runs a command.
"""

from __future__ import annotations

from pathlib import PurePosixPath

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_package_scope as package_scope
    from . import autocode_verification_commands as verification_commands
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_package_scope as package_scope
    import autocode_verification_commands as verification_commands

ROOT = ""


def project_of(root, path) -> str:
    """The innermost directory around ``path`` with its own manifest, or ``ROOT``."""
    parts = PurePosixPath(path).parent.parts
    for end in range(len(parts), 0, -1):
        project = "/".join(parts[:end])
        directory = package_scope.directory_in(root, project)
        if directory is not None and any((directory / name).is_file() for name in package_scope.MANIFESTS):
            return project
    return ROOT


def where(project) -> str:
    return f"{project}/" if project else "the repository root"


def label(project) -> str:
    """The key of a companion suite's receipts in the proof's checks."""
    return project or "."


def _tests_of(project, own, test_files) -> list[str]:
    """The test files of ``project``, leaving out those of the proof's own project ``own``."""
    return [
        path
        for path in test_files
        if (project == ROOT or package_scope.contains(project, path))
        and not (own and package_scope.contains(own, path))
    ]


def find(root, framework, root_framework, code_paths, test_files, detect_in) -> tuple[list, list[str]]:
    """(suites, notes): the suite of each other project around ``code_paths`` the proof's ``framework`` cannot
    run the tests of.

    ``root_framework`` is the suite detected at the repository root; ``test_files`` the original code's test
    files; ``detect_in(directory)`` detects a suite with that directory as the project root. A project the
    proof's suite may hold the tests of (it runs some of them, or it cannot be targeted at all, such as
    ``make test``) gets no second suite, and neither does a project with no tests of its own.
    """
    if framework is None:
        return [], []
    own = getattr(framework, "directory", None) or ROOT
    suites, notes = [], []
    for project in sorted({project_of(root, path) for path in code_paths}):
        tests = _tests_of(project, own, test_files)
        if project == own or not tests or not verification_commands.cannot_run(framework, tests):
            continue
        found = root_framework if project == ROOT else package_scope.detect(root, project, detect_in)
        if found is None:
            notes.append(
                f"Code in {where(project)} changed and the proof's suite `{framework.suite}` cannot run its tests, "
                "but no suite was found for it, so its existing tests were not run"
            )
            continue
        suites.append((project, found))
        notes.append(
            f"Code in {where(project)} changed and the proof's suite `{framework.suite}` cannot run its tests, so "
            f"its own suite `{found.suite}` ran on the original code and with the change"
        )
    return suites, notes


def guard_suites(root, framework, root_framework, test_files, names, detect_in) -> tuple[list[dict], list[str]]:
    """(suites, notes): the projects outside the proof's suite whose test files name a guard's test.

    ``test_files`` are the original code's test files and ``names(path)`` the guard test names a file names
    (none when it cannot be read). Each suite is a dict: ``project``, ``framework`` (its own suite, detected
    as in ``find``), ``files``, ``command`` (that suite targeted at the files) and ``guards`` (the names).
    A file the proof's suite may run is the proof's own to decide, and a project whose suite cannot target
    its files by name gets a note instead.
    """
    if framework is None:
        return [], []
    found_in: dict = {}
    for path in sorted(test_files):
        named = names(path) if verification_commands.cannot_run(framework, [path]) else ()
        if named:
            files, guards = found_in.setdefault(project_of(root, path), ([], set()))
            files.append(path)
            guards.update(named)
    suites, notes = [], []
    for project, (files, guards) in sorted(found_in.items()):
        found = root_framework if project == ROOT else package_scope.detect(root, project, detect_in)
        command = found.targeted(files) if found is not None else None
        which = f"The guards' tests in {', '.join(files)} are in {where(project)}, whose tests the proof's suite "
        if command is None:
            notes.append(
                f"{which}`{framework.suite}` cannot run, and no suite there runs them by file, so they were not run"
            )
            continue
        suites.append(
            {"project": project, "framework": found, "files": files, "command": command, "guards": sorted(guards)}
        )
        notes.append(
            f"{which}`{framework.suite}` cannot run, so `{command}` ran them on the original code and with the change"
        )
    return suites, notes


def guard_results(on_base, on_change) -> dict:
    """What a guard suite's two runs show: ``passing`` (passed in both), ``failing`` (failed with the change),
    and ``complete``, False when either run did not finish or reported no complete per-test results."""
    runs = [on_base, on_change]
    if not all(command_receipt.completed(run) and not run.get("timed_out") for run in runs) or not all(
        (run.get("results") or {}).get("complete") for run in runs
    ):
        return {"passing": [], "failing": [], "complete": False}
    before, after = on_base["results"], on_change["results"]
    return {
        "passing": sorted(set(before.get("passed") or []) & set(after.get("passed") or [])),
        "failing": sorted(after.get("failed") or []),
        "complete": True,
    }


def judge(project, command, on_base, on_change) -> tuple[list[str], list[str]]:
    """(failures, unverified) from one companion suite's run on the original code and with the change."""
    suite = f"The {project}/ suite `{command}`" if project else f"The repository root's suite `{command}`"
    for receipt, when in ((on_base, "on the original code"), (on_change, "with the change")):
        if not command_receipt.completed(receipt) or receipt.get("timed_out"):
            return [], [f"{suite} did not finish {when}, so it cannot show whether the change breaks a test there"]
    before, after = on_base.get("results"), on_change.get("results")
    if before is not None and after is not None:
        broken = sorted(set(before.get("passed") or []) - set(after.get("passed") or []))
        if broken:
            return [
                f"{suite}: tests that pass on the original code do not pass with the change: "
                + ", ".join(broken[:10])
                + (f" and {len(broken) - 10} more" if len(broken) > 10 else "")
            ], []
        return [], []
    if on_base.get("exit_code") != 0:
        return [], [
            f"{suite} fails on the original code (exit {on_base.get('exit_code')}) and reports no per-test "
            "results, so it cannot show whether the change breaks a test there"
        ]
    if on_change.get("exit_code") != 0:
        return [f"{suite} passes on the original code and fails with the change (exit {on_change['exit_code']})"], []
    return [], []


def verdict(current, failures, unverified, *, fail, unverified_verdict) -> str:
    """The proof's verdict once a companion suite's findings are added: they only ever lower it."""
    if failures:
        return fail
    return unverified_verdict if unverified and current != fail else current
