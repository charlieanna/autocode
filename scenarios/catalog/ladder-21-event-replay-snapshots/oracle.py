from harness.oracle import python_change_checks, run_checks


def check(project, scenario, run=None):
    return python_change_checks(project, scenario, "journal") + run_checks(run, workflow="build")
