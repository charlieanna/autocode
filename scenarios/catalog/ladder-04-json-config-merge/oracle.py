import sys
import shutil

from harness.oracle import IGNORED, Check, hidden_tests, non_stdlib_imports, run as run_command, run_checks, scratch_copy, tail, test_names

def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        # The brief requires unittest tests, not an importable tests package.
        suite = run_command([sys.executable, "-m", "unittest", "discover", "-s", "tests"], copy, timeout=300)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    names = test_names(project / "tests")
    checks.append(Check("delivered_tests_present", bool(names), f"{len(names)} test methods"))
    # The requested tests must exercise the delivered implementation: run them
    # alone against the original empty project. A timeout is not a useful failure.
    against_seed = None
    if (project / "tests").is_dir():
        with scratch_copy(scenario.seed) as original:
            shutil.copytree(project / "tests", original / "tests", ignore=IGNORED, dirs_exist_ok=True)
            against_seed = run_command([sys.executable, "-m", "unittest", "discover", "-s", "tests"], original, timeout=30)
    exercises_implementation = against_seed is not None and 0 < against_seed.returncode < 127
    checks.append(Check("tests_require_implementation", exercises_implementation,
                        tail(against_seed) if against_seed is not None else "no delivered tests"))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    readme = project / "README.md"
    documented = readme.is_file() and "-m configmerge" in readme.read_text(encoding="utf-8", errors="replace")
    checks.append(Check("usage_documented", documented, "README includes CLI invocation"))
    return checks + run_checks(run, workflow="build")
