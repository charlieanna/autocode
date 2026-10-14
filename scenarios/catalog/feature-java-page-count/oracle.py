"""Judge Java behavior and real Surefire fail-to-pass evidence independently."""

import shutil
import xml.etree.ElementTree as ET

from harness.oracle import Check, named_proof_checks, run_checks, scratch_copy, tail
from harness.oracle import run as execute

REPORT = "target/surefire-reports/TEST-example.PageCountTest.xml"
PRODUCT = "src/main/java/example/PageCount.java"
REGRESSION = "test_c1_partial_page_is_counted"
GUARD = "test_c2_exact_multiple_remains_two_pages"
COMMAND = ["mvn", "-o", "-B", "-q", "-Dtest=PageCountTest", "test"]


def suite(project):
    # Neither a previous XML file nor compiled classes may supply this run.
    shutil.rmtree(project / "target", ignore_errors=True)
    process = execute(COMMAND, project, timeout=120)
    report = project / REPORT
    passed, failed = set(), set()
    complete = False
    if report.is_file():
        try:
            root = ET.parse(report).getroot()
            cases = root.findall("testcase")
            names = [row.get("name") for row in cases]
            complete = (
                root.tag == "testsuite"
                and int(root.get("tests", "-1")) == len(cases)
                and len(set(names)) == len(names)
                and not any(row.find("error") is not None or row.find("skipped") is not None for row in cases)
            )
            for row in cases:
                (failed if row.find("failure") is not None else passed).add(row.get("name"))
        except (ET.ParseError, ValueError):
            pass
    return process, complete, passed, failed


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as candidate:
        process, complete, passed, failed = suite(candidate)
        checks.append(
            Check("real_junit_suite_passes", process.returncode == 0 and complete and not failed, tail(process))
        )
        checks.append(Check("guard_and_regression_are_real_cases", {GUARD, REGRESSION} <= passed, repr(sorted(passed))))
        # Hidden behavior does not trust the delivered tests or their XML.
        classes = candidate / "oracle-classes"
        classes.mkdir()
        compiled = execute(
            ["javac", "-d", str(classes), str(candidate / PRODUCT), str(scenario.dir / "hidden/PageCountOracle.java")],
            candidate,
        )
        hidden = (
            execute(["java", "-cp", str(classes), "PageCountOracle"], candidate)
            if compiled.returncode == 0
            else compiled
        )
        checks.append(Check("hidden_java_behavior", hidden.returncode == 0, tail(hidden)))
    with scratch_copy(project) as original:
        shutil.copyfile(scenario.seed / PRODUCT, original / PRODUCT)
        process, complete, passed, failed = suite(original)
        checks.append(
            Check(
                "regression_fails_on_original_product",
                process.returncode != 0 and complete and REGRESSION in failed,
                tail(process),
            )
        )
        checks.append(Check("existing_guard_passes_on_original", complete and GUARD in passed, repr(sorted(passed))))
    return checks + named_proof_checks(run, scenario) + run_checks(run, workflow="build")
