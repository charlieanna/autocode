"""Named JUnit proof and preservation through real owned command executions.

The small Maven-shaped gate makes XML from Java fixture behavior. It is kept
outside the Git project so candidates cannot rewrite the trusted executable.
The catalog's real Maven/JUnit scenario separately qualifies the ecosystem.
"""

import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_verification_schedule as schedule
import autocode_verify as verify

from .test_verify import Project

PRODUCT = "src/main/java/example/PageCount.java"
TEST = "src/test/java/example/PageCountTest.java"
REPORT = "target/surefire-reports/TEST-example.PageCountTest.xml"
C1 = "example.PageCountTest::test_c1_partial"
C2 = "example.PageCountTest::test_c2_exact"
POM = "<project><guardExpected>2</guardExpected></project>\n"
SOURCE = "class PageCount { static final int PARTIAL = 1; static final int EXACT = 2; }\n"
GUARD = "class PageCountTest { static final int GUARD_EXPECTED = 2; void test_c2_exact() {} }\n"
REGRESSION = (
    "class PageCountTest { static final int GUARD_EXPECTED = 2; void test_c2_exact() {} void test_c1_partial() {} }\n"
)
GATE = r"""
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

root = Path.cwd()
pom = root / "pom.xml"
if not pom.is_file():
    if "--silent-no-project" not in sys.argv:
        print("[ERROR] The goal you specified requires a project to execute but there is no POM in this directory.")
    raise SystemExit(1)
source = (root / "src/main/java/example/PageCount.java").read_text()
tests = (root / "src/test/java/example/PageCountTest.java").read_text()
value = lambda name, text: int(re.search(r"\b" + name + r"\s*=\s*(\d+)", text).group(1))
if "--no-output" in sys.argv:
    raise SystemExit(0)
if "--report-after-fix" in sys.argv and value("PARTIAL", source) != 2:
    print("[ERROR] The goal you specified requires a project to execute but there is no POM in this directory.")
    raise SystemExit(1)
outcomes = []
if "test_c2_exact()" in tests:
    expected = value("GUARD_EXPECTED", tests)
    selected = re.search(r"<guardExpected>(\d+)</guardExpected>", pom.read_text())
    if selected and int(selected.group(1)) != 2:
        expected = int(selected.group(1))
    outcomes.append(("test_c2_exact", value("EXACT", source) == expected))
if "test_c1_partial()" in tests:
    outcomes.append(("test_c1_partial", "--vacuous-regression" in sys.argv or value("PARTIAL", source) == 2))
failed = sum(not passed for _, passed in outcomes)
suite = ET.Element("testsuite", name="example.PageCountTest", tests=str(len(outcomes)), failures=str(failed), errors="0", skipped="0")
for name, passed in outcomes:
    case = ET.SubElement(suite, "testcase", classname="example.PageCountTest", name=name, time="0.001")
    if not passed:
        ET.SubElement(case, "failure", message="behavior differs")
report = root / "target/surefire-reports/TEST-example.PageCountTest.xml"
report.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(report, encoding="utf-8", xml_declaration=True)
print("Ran", len(outcomes), "test(s), failures:", failed)
raise SystemExit(1 if failed else 0)
"""


class JUnitProofTests(unittest.TestCase):
    def setUp(self):
        runtime = tempfile.TemporaryDirectory(prefix="junit-trusted-runner-")
        self.addCleanup(runtime.cleanup)
        self.runner = Path(runtime.name).resolve() / "mvn"
        self.runner.write_text(f"#!{sys.executable}\n" + GATE)
        self.runner.chmod(0o700)

    def project(self, files=None):
        project = Project(
            files if files is not None else {"pom.xml": POM, PRODUCT: SOURCE, TEST: GUARD, ".gitignore": "target/\n"}
        )
        self.addCleanup(project.close)
        return project

    def proof(self, project, *, arguments=(), new_behavior=False):
        command = shlex.join([str(self.runner), "test", *arguments])
        framework = verify.command_framework(command, test_report="junit:" + REPORT)
        baseline = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=command,
            timeout=15,
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=command,
            base_suite=baseline,
            timeout=15,
            new_behavior=new_behavior,
        )
        return result, baseline

    def candidate(self, project, *, exact=2, tests=REGRESSION, pom=POM):
        project.write(
            {
                "pom.xml": pom,
                PRODUCT: SOURCE.replace("PARTIAL = 1", "PARTIAL = 2").replace("EXACT = 2", f"EXACT = {exact}"),
                TEST: tests,
            }
        )

    def test_named_flip_is_verified_and_owned_xml_survives_scratch_cleanup_and_detects_edits(self):
        project = self.project()
        self.candidate(project)
        result, baseline = self.proof(project)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([C1], result["fail_to_pass"])
        self.assertIn(C2, result["pass_to_pass"])
        self.assertFalse((project.evidence / "scratch/candidate").exists())
        for receipt in [baseline["receipt"], *result["checks"].values()]:
            with self.subTest(command=receipt["command"], artifact=receipt.get("test_report")):
                self.assertTrue(schedule.intact(receipt, root=project.evidence))
                self.assertTrue(Path(receipt["test_report"]["path"]).is_file())
        receipt = result["checks"]["regression_on_candidate"]
        artifact = Path(receipt["test_report"]["path"])
        artifact.write_bytes(artifact.read_bytes().replace(b"test_c1_partial", b"test_c1_changed"))
        self.assertFalse(schedule.intact(receipt, root=project.evidence))

    def test_a_stale_report_cannot_replace_output_the_selected_command_never_produced(self):
        project = self.project()
        self.candidate(project)
        project.evidence.mkdir()
        stale = project.root / REPORT
        stale.parent.mkdir(parents=True)
        stale.write_text('<testsuite tests="1"><testcase classname="Forged" name="passes"/></testsuite>')
        command = shlex.join([str(self.runner), "test", "--no-output"])
        framework = verify.command_framework(command, test_report="junit:" + REPORT)
        receipt = verify.run_suite(framework, command, project.root, project.evidence, "stale-control", timeout=15)
        self.assertFalse(stale.exists())
        self.assertIsNone(receipt["results"])
        self.assertIn("missing", receipt["report_error"].lower())
        result, _ = self.proof(project, arguments=("--no-output",))
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("without reporting any test result" in reason for reason in result["unverified"]), result)

    def test_a_regression_that_already_passes_on_base_cannot_prove_the_fix(self):
        project = self.project()
        self.candidate(project)
        result, _ = self.proof(project, arguments=("--vacuous-regression",))
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertEqual([], result["fail_to_pass"])
        self.assertTrue(any("do not reproduce the bug" in reason for reason in result["failures"]), result)

    def test_removing_a_named_base_guard_fails_even_when_the_new_regression_flips(self):
        project = self.project()
        self.candidate(project, tests="class PageCountTest { void test_c1_partial() {} }\n")
        result, _ = self.proof(project)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIn(C1, result["fail_to_pass"])
        self.assertTrue(any(C2 in reason and "did not pass" in reason for reason in result["failures"]), result)

    def test_retaining_base_definitions_exposes_broken_product_hidden_by_test_or_pom_edits(self):
        for boundary in ("test-body", "pom-setting"):
            with self.subTest(boundary=boundary):
                project = self.project()
                self.candidate(
                    project,
                    exact=999,
                    tests=REGRESSION.replace("GUARD_EXPECTED = 2", "GUARD_EXPECTED = 999")
                    if boundary == "test-body"
                    else REGRESSION,
                    pom=POM.replace("<guardExpected>2", "<guardExpected>999") if boundary == "pom-setting" else POM,
                )
                result, _ = self.proof(project)
                self.assertEqual(verify.FAIL, result["verdict"], result)
                candidate = result["checks"]["suite_on_candidate"]["results"]
                self.assertEqual([], candidate["failed"])
                self.assertIn(C2, candidate["passed"])
                retained = result["checks"]["suite_base_definition_on_candidate"]["results"]
                self.assertIn(C2, retained["failed"])
                self.assertTrue(any(C2 in reason for reason in result["failures"]), result)

    def test_first_java_suite_requires_new_behavior_pinned_absence_and_a_known_no_project_diagnostic(self):
        cases = (
            ("first-feature", {"app.py": "VALUE = 7\n"}, True, (), verify.PASS),
            ("first-bugfix", {"app.py": "VALUE = 7\n"}, False, (), verify.UNVERIFIED),
            ("unknown-diagnostic", {"app.py": "VALUE = 7\n"}, True, ("--silent-no-project",), verify.UNVERIFIED),
            ("existing-java", None, True, ("--report-after-fix",), verify.UNVERIFIED),
        )
        for name, files, new_behavior, arguments, expected in cases:
            with self.subTest(case=name):
                project = self.project(files)
                self.candidate(project)
                result, _ = self.proof(project, arguments=arguments, new_behavior=new_behavior)
                self.assertEqual(expected, result["verdict"], result)
                if expected == verify.PASS:
                    self.assertIn(C1, result["fail_to_pass"])
                    self.assertTrue(any("pinned base has no project" in note for note in result["notes"]), result)
                else:
                    self.assertTrue(result["unverified"], result)


if __name__ == "__main__":
    unittest.main()
