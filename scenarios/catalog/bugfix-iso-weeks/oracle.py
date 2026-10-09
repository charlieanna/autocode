from harness.oracle import python_change_checks


def check(project, scenario):
    return python_change_checks(project, scenario, package="timesheet")
