"""Correct fix (hidden tests) through the full bug-fix path: investigate, then plan the fix with plan
review and the user's approval, never gathering requirements, and no questions. The stage count is
not judged while every job takes the full path (the short path for small fixes is off)."""

from harness.oracle import python_change_checks, run_checks


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="pager")
    checks += run_checks(run, workflow="bugfix", no_requirements=True, plan_approved=True, max_questions=0)
    return checks
