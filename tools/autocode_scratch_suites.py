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

Those are rules about the layout, and a layout they do not know put a file where it cannot load. So
``run_tests`` reads the run: a file that did not load, or that no suite ran, is looked for by running it from
the directories around it, as the regression proof looks (autocode_test_homes), and the files then run
again from where they load.
"""

from __future__ import annotations

try:
    from . import autocode_companion_suites as companions
    from . import autocode_package_scope as package_scope
    from . import autocode_source_binding as source_binding
    from . import autocode_test_homes as test_homes
    from . import autocode_verification_commands as verification_commands
    from . import autocode_wrapped_runner as wrapped_runner
except ImportError:
    import autocode_companion_suites as companions
    import autocode_package_scope as package_scope
    import autocode_source_binding as source_binding
    import autocode_test_homes as test_homes
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
    merged = {
        **failed,
        "command": " ; ".join(run.get("command") or "" for run in receipts),
        "timed_out": any(run.get("timed_out") for run in receipts),
        "results": results,
        "results_expected": any(run.get("results_expected") for run in receipts),
        "suites": receipts,
    }
    if binding := source_binding.merged(receipts):
        source_binding.attach(merged, binding)
    return merged


def run_selected(root, tests, selected, execute, *, acceptance_preflight=False):
    """Preflight the entire selected set before executing even its first suite."""
    if isinstance(selected, str):
        return {"error": selected, "results": None}
    if acceptance_preflight and (refused := wrapped_runner.execution_refusal(root, tests, selected)):
        return refused
    return merged(execute(framework, command, label) for framework, command, label in selected)


def run_tests(root, framework, tests, detect_in, execute, *, acceptance_preflight=False):
    """Run ``tests`` with the suites the rules choose (``suites``). When a file does not load there, or no
    suite there runs one, and running them from the directories around them finds where they load, they run
    again from there, and that receipt is the result."""
    selected = suites(root, framework, tests, detect_in)
    receipt = run_selected(root, tests, selected, execute, acceptance_preflight=acceptance_preflight)
    if receipt.get("acceptance_refusal") or not (isinstance(selected, str) or test_homes.loads(receipt) is False):
        return receipt
    found = _where_they_load(root, framework, list(tests), detect_in, execute)
    return run_selected(root, tests, found, execute, acceptance_preflight=acceptance_preflight) if found else receipt


def _where_they_load(root, framework, tests, detect_in, execute):
    """``suites`` for ``tests`` from where each was seen to load, or None when none moved or one runs nowhere."""
    state: dict = {}
    chosen = companions.test_suites(root, framework, tests, detect_in)
    probe = lambda suite, command, label: execute(suite, command, f"{LABEL}-homes-{label.replace('/', '-')}")  # noqa: E731
    test_homes.settle(state, root, framework, chosen, tests, detect_in, probe)
    if not test_homes.moved(state):
        return None
    own, others, _notes = test_homes.place(state, root, framework, chosen, tests, detect_in)
    groups = [{"project": getattr(own, "directory", None) or companions.ROOT, "framework": own, "files": []}, *others]
    groups[0]["files"] = [test for test in tests if not any(test in group["files"] for group in others)]
    found = []
    for group in groups:
        suite, files, project = group["framework"], group["files"], group["project"]
        command = suite.targeted(files) if suite is not None and files else None
        if files and not command:
            return None
        if files:
            found.append((suite, command, f"{LABEL}-{project.replace('/', '-')}" if project else LABEL))
    return found


def explicit(root, tests, framework, command, execute, complete_results, *, acceptance_preflight=False):
    receipt = run_selected(
        root, tests, [(framework, command, "scratch-command")], execute, acceptance_preflight=acceptance_preflight
    )
    if receipt.get("acceptance_refusal") or framework is None:
        return receipt
    if receipt["exit_code"] == 0 and not receipt["timed_out"] and not complete_results(receipt):
        return {**receipt, "error": "Test command reported zero tests or incomplete per-test results"}
    if (
        receipt["exit_code"] == 0
        and not receipt["timed_out"]
        and not (receipt["results"]["passed"] or receipt["results"]["failed"])
    ):
        return {**receipt, "error": "Test command reported only skipped tests"}
    return receipt
