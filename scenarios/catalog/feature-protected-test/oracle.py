"""Express shipping works, standard shipping is unchanged (hidden tests), and the tests the project
started with still pass exactly as they were written: a change may add tests, never weaken one."""
import shutil

from harness.oracle import Check, python_change_checks, python_tests, scratch_copy, tail


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, "shop")
    with scratch_copy(project) as copy:
        shutil.copy2(scenario.seed / "tests" / "test_shipping.py", copy / "tests" / "test_shipping.py")
        original = python_tests(copy)
    checks.append(Check("original_tests_pass_as_written", original.returncode == 0, tail(original)))
    return checks
