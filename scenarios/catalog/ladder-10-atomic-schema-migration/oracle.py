from harness.oracle import Check, python_change_checks, run_checks


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, "app")
    checks.append(Check("readme_delivered", (project / "README.md").is_file()))
    return checks + run_checks(run, workflow="build")
