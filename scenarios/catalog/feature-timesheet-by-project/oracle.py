from harness.oracle import Check, python_change_checks


def check(project, scenario):
    checks = python_change_checks(project, scenario, package="timesheet")
    readme = project / "README.md"
    checks.append(Check("readme_documents_option", readme.is_file() and "--by-project" in readme.read_text()))
    return checks
