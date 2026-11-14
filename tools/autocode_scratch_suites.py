"""Which suites run a set of test files in a scratch copy, one per project, and their merged receipt.

Before the Builder starts, the runner runs the Tester's new tests on today's code (the acceptance proof) by
targeting the suite detected at the repository root. A live bug fix in a repository with a Node suite at its
root and a Python project in backend/ (2026-11-12) handed both test files to the root's Node suite, which
targeted only the Node file: the Python tests were never collected, and the runner told the Tester three
times that they did not run, until it wrapped them in a Node test file and the run paused. The proof's own
suite choice had the same fault (#2889, #3246).

``suites`` keeps one suite when it runs every file. Otherwise each file runs with the suite of the project
around it (``autocode_companion_suites.project_of``), as ``--package`` would run it, and ``merged`` joins
their receipts so each test is reported by the suite that ran it. A file that no suite there runs is
refused by name instead of being left out.
"""

from __future__ import annotations

try:
    from . import autocode_companion_suites as companions
    from . import autocode_package_scope as package_scope
    from . import autocode_verification_commands as verification_commands
    from . import autocode_wrapped_runner as wrapped_runner
except ImportError:
    import autocode_companion_suites as companions
    import autocode_package_scope as package_scope
    import autocode_verification_commands as verification_commands
    import autocode_wrapped_runner as wrapped_runner

LABEL = "scratch-tests"
_RESULT_LISTS = ("passed", "failed", "skipped", "collection_errors", "uncollected")


def suites(root, framework, tests, detect_in):
    """[(framework, command, label)] that run every one of ``tests``, or a message naming the files none runs.

    ``framework`` is the suite detected at ``root``; ``detect_in(directory)`` detects one in a project."""
    tests = list(tests)
    command = framework.targeted(tests) if framework else None
    if not command and (nested := package_scope.for_tests(root, framework, tests, detect_in)):
        return [(nested, nested.targeted(tests), LABEL)]
    if command and not any(verification_commands.cannot_run(framework, [test]) for test in tests):
        return [(framework, command, LABEL)]
    groups: dict[str, list[str]] = {}
    for test in tests:
        # A file the root's suite runs stays there; only the files it leaves out go to their own project.
        runs_here = framework is not None and not verification_commands.cannot_run(framework, [test])
        groups.setdefault(companions.ROOT if runs_here else companions.project_of(root, test), []).append(test)
    found = []
    for project, files in sorted(groups.items()):
        suite = framework if project == companions.ROOT else package_scope.detect(root, project, detect_in)
        command = suite.targeted(files) if suite is not None else None
        left = [file for file in files if verification_commands.cannot_run(suite, [file])]
        if not command or left:
            return verification_commands.untargeted(suite, left or files)
        found.append((suite, command, f"{LABEL}-{project.replace('/', '-')}" if project else LABEL))
    return found


def merged(receipts):
    """One receipt for the runs of several suites: the first failed run's (else the first) with every run's
    results joined, ``exit_code`` and ``timed_out`` from any run that failed, and the runs under ``suites``.
    Results are None unless every run reported them."""
    receipts = list(receipts)
    if len(receipts) == 1:
        return receipts[0]
    failed = next((run for run in receipts if run.get("exit_code") or run.get("timed_out")), receipts[0])
    every = [run.get("results") for run in receipts]
    results = None
    if all(isinstance(found, dict) for found in every):
        results = {name: sorted({test for found in every for test in found.get(name) or []}) for name in _RESULT_LISTS}
        results["total"] = sum(found.get("total") or 0 for found in every)
        results["complete"] = all(found.get("complete") is True for found in every)
    return {
        **failed,
        "command": " ; ".join(run.get("command") or "" for run in receipts),
        "timed_out": any(run.get("timed_out") for run in receipts),
        "results": results,
        "results_expected": any(run.get("results_expected") for run in receipts),
        "suites": receipts,
    }


def run_selected(root, tests, selected, execute, *, acceptance_preflight=False):
    """Preflight the entire selected set before executing even its first suite."""
    if isinstance(selected, str):
        return {"error": selected, "results": None}
    if acceptance_preflight and (refused := wrapped_runner.execution_refusal(root, tests, selected)):
        return refused
    return merged(execute(framework, command, label) for framework, command, label in selected)
