"""Correct boundary fix; the one assertion of test_fee_schedule that asserted the bug changes in place, the rest stay."""

from harness.oracle import bugfix_entry, python_change_checks, run_checks


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="shop")
    checks += run_checks(
        run, workflow="bugfix", after_requirements=bugfix_entry(run), plan_approved=True, max_questions=0
    )
    return checks
