"""Classify completed test-suite evidence without a runner dependency."""

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_verification_schedule as schedule


def suite_health(receipt) -> str:
    """passing | failing_tests (some pass) | failing (no per-test detail) | broken | timeout."""
    results = receipt.get("results")
    if receipt["timed_out"]:
        return "timeout"
    if not command_receipt.completed(receipt):
        return "broken"  # interrupted ownership cannot establish passing tests
    if receipt["exit_code"] in (126, 127):
        return "broken"  # the command itself could not run
    if receipt.get("results_expected") and results is None:
        return "broken"  # the runner ended before reporting any test
    if results is not None and not schedule.complete_results(receipt):
        return "broken"
    if results is not None and (results["total"] == 0 or (results["complete"] and not results["passed"])):
        return "broken"
    if receipt["exit_code"] == 0:
        return "passing"
    return "failing" if results is None else "failing_tests"
