import sys

from harness.oracle import Check, hidden_tests, non_stdlib_imports, run_checks, scratch_copy, tail, test_names
from harness.oracle import run as run_command


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        # The brief requires unittest tests, not an importable tests package.
        suite = run_command([sys.executable, "-m", "unittest", "discover", "-s", "tests"], copy, timeout=300)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    names = test_names(project / "tests")
    checks.append(Check("delivered_tests_present", len(names) >= 3, f"{len(names)} test methods"))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    readme = project / "README.md"
    documented = readme.is_file() and "-m wordfreq" in readme.read_text(encoding="utf-8", errors="replace")
    checks.append(Check("usage_documented", documented, "README includes CLI invocation"))
    return checks + run_checks(run, workflow="build")
