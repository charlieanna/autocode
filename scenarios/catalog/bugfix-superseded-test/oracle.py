"""Correct boundary fix; the existing test that asserted the bug is replaced in place, not deleted (#2748)."""

from harness.oracle import bugfix_entry, python_change_checks, run_checks


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="shop")
    checks += run_checks(
        run, workflow="bugfix", after_requirements=bugfix_entry(run), plan_approved=True, max_questions=0
    )
    return checks
