from harness.oracle import python_change_checks, run_checks


def check(project, scenario, run=None):
    return python_change_checks(project, scenario, package="safezip") + run_checks(
        run, workflow="bugfix", after_requirements="investigate_bug", plan_approved=True, max_questions=0
    )
