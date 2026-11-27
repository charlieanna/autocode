"""Selected-test mutation outcomes, including an explicit stop after failure."""

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_python_tests as python_tests
    from . import autocode_runner_reports as runner_reports
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_python_tests as python_tests
    import autocode_runner_reports as runner_reports
    import autocode_verification_schedule as schedule

STOP_POLICY = "stop_after_selected_failure_v1"
# A mutant run again on a changed candidate with only the tests that killed it on the prior candidate
# (#4217). It is a selection rule like the affected-test ones: a kill it observes is a kill on this
# candidate by a subset of the selected tests, and a pass sends the mutant to its whole site selection.
PRIOR_KILL_RULE = "prior_kill"


def stop_command(command):
    """Keep collection and selectors, adding the runner's early-stop option first."""
    invocation = python_tests.parse(command, include_owned=True)
    arguments = list(invocation.arguments)
    if "--" in arguments:
        return command  # Do not reinterpret a positional argument as a runner option.
    if invocation.kind == "unittest":
        index = 1 if arguments[:1] == ["discover"] else 0
        arguments.insert(index, "-f")
    else:
        arguments = ["-x", *arguments, "--maxfail=1"]  # Override an earlier maxfail setting.
    return invocation.render(arguments=arguments)


def stops_after_failure(command):
    # A package's run enters its directory first (``cd PACKAGE && RUNNER``, autocode_package_scope.command):
    # the runner inside is the one that stops. Read as the whole command, it was no runner at all, so every
    # mutant a package's test caught counted as not run and each changed line there was left without evidence.
    if runner_reports.runner(package_scope.within(command)) in runner_reports.STOPS_AFTER_FAILURE:
        return True
    invocation = python_tests.parse(package_scope.within(command), include_owned=True)
    if invocation is None:
        return False
    arguments = list(invocation.arguments)
    if "--" in arguments:
        return False
    if invocation.kind == "unittest":
        if arguments[:1] == ["discover"]:
            arguments = arguments[1:]
        return arguments[:1] == ["-f"]
    # The runner appends its owned JUnit capture after the execution options.
    if arguments and arguments[-1].startswith("--junitxml="):
        arguments.pop()
    return invocation.kind == "pytest" and arguments[:1] == ["-x"] and arguments[-1:] == ["--maxfail=1"]


def selected(receipt, tests):
    """The receipt as if only ``tests`` had run, for a runner that selects tests by a name pattern (#3817).

    ``go test -run`` also runs a selected subtest's parent and siblings, and an unanchored
    ``--test-name-pattern`` can match a test beside the selected one. Those results are dropped, so
    ``outcome`` judges exactly the selected tests. A test beside them that failed while every selected
    test passed leaves the receipt as it was: its failure, not theirs, set the exit code, so the run
    proves nothing either way. Uncollected and collection-error entries are left as they were.
    """
    results = (receipt or {}).get("results")
    if not isinstance(results, dict) or not all(isinstance(results.get(key), list) for key in ("passed", "failed")):
        return receipt
    chosen = set(tests)
    if set(results["failed"]) - chosen and not set(results["failed"]) & chosen:
        return receipt
    kept = {
        key: [test for test in results[key] if test in chosen]
        for key in ("passed", "failed", "skipped")
        if isinstance(results.get(key), list)
    }
    total = sum(len(group) for group in kept.values())
    return {**receipt, "results": {**results, **kept, "total": total}}


def outcome(receipt, selected, *, stopped=False):
    """Require a complete reference/survivor; a selected failure can end a mutant.

    ``stopped`` is granted only to the collector's explicit early-stop command.
    Its completed observed results must be a subset of the reference's exact
    passing selection. Missing results alone never prove a kill or a survivor.
    The caller separately authenticates capture ownership when enforcing proof.
    """
    if not receipt or receipt.get("error") or receipt.get("interrupted"):
        return "not_run", []
    if receipt.get("timed_out"):
        return "timeout", []
    results = receipt.get("results") or {}
    if not schedule.complete_results(receipt) or any(
        results.get(key) for key in ("setup_errors", "skipped", "collection_errors", "uncollected")
    ):
        return "not_run", []
    passed, failed = set(results["passed"]), set(results["failed"])
    seen, expected = passed | failed, set(selected)
    if seen != expected and not (stopped and failed and seen <= expected):
        return "not_run", []
    if failed and type(receipt.get("exit_code")) is int and receipt["exit_code"] != 0:
        return "killed", sorted(failed)
    if not failed and receipt.get("exit_code") == 0:
        return "survived", sorted(passed)
    return "not_run", []
