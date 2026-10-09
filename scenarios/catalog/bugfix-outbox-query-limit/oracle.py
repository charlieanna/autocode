from harness.oracle import python_change_checks, run_checks


def check(project, scenario, run=None):
    return python_change_checks(project, scenario, package="outbox") + run_checks(
        run, workflow="bugfix", no_requirements=True, plan_approved=True, max_questions=0
    )
