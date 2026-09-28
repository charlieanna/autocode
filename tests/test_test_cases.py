"""Small features: acceptance criteria written as English examples and proven by the runner.

The approved one-milestone plan marks a criterion ``verification_method: "test: test_c2_..."``;
the runner's proof then requires that test to pass with the change and not without it.
See autocode_test_cases and docs/workflow.md.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_regression as regression
import autocode_test_cases as test_cases
import autocode_verify as verify
from tests.test_verify import Project

EXAMPLE = {"id": "C2", "criterion": "Given calc.sub; when sub(5, 3) runs; then it returns 2",
           "verification_method": "test: test_c2_subtracts", "human_review": False}
ORDINARY = {"id": "C1", "criterion": "The README documents sub", "verification_method": "Read README.md",
            "human_review": False}
SEED = {"calc.py": "def add(a, b):\n    return a + b\n",
        "test_calc.py": "import unittest\nfrom calc import add\n\n\nclass CalcTests(unittest.TestCase):\n"
                        "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n"}
FEATURE = {"calc.py": SEED["calc.py"] + "\n\ndef sub(a, b):\n    return a - b\n",
           "test_calc.py": SEED["test_calc.py"].replace("from calc import add", "from calc import add, sub")
           + "\n    def test_c2_subtracts(self):\n        self.assertEqual(2, sub(5, 3))\n"}


def feature_state(project, criteria, milestones=1):
    return {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
            "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": criteria,
                                       "milestones": [{"id": f"M{n}"} for n in range(1, milestones + 1)]}}}


class ContractCasesTests(unittest.TestCase):
    def test_only_criteria_marked_test_are_cases(self):
        state = {"goal_contract": {"body": {"milestones": [{"id": "M1"}], "acceptance_criteria": [
            ORDINARY, EXAMPLE, {**EXAMPLE, "id": "C3", "verification_method": "  TEST: test_c3_x"}]}}}
        self.assertEqual([{"id": "C2", "text": EXAMPLE["criterion"]}, {"id": "C3", "text": EXAMPLE["criterion"]}],
                         test_cases.contract_cases(state))
        self.assertEqual("C2: " + EXAMPLE["criterion"], test_cases.case_text(test_cases.contract_cases(state)[0]))

    def test_a_plan_with_several_milestones_keeps_ordinary_criteria(self):
        state = {"goal_contract": {"body": {"milestones": [{"id": "M1"}, {"id": "M2"}],
                                            "acceptance_criteria": [EXAMPLE]}}}
        self.assertEqual([], test_cases.contract_cases(state))
        self.assertEqual("", test_cases.builder_note(state))
        self.assertEqual([], test_cases.contract_cases({}))

    def test_the_proof_is_required_only_when_the_plan_names_tests(self):
        project = type("Committed", (), {"base": "b"})()
        self.assertTrue(regression.required(feature_state(project, [ORDINARY, EXAMPLE])))
        self.assertFalse(regression.required(feature_state(project, [ORDINARY])))
        self.assertFalse(regression.required(feature_state(project, [EXAMPLE], milestones=2)))


class NewBehaviorVerifyTests(unittest.TestCase):
    """Real repositories and real unittest runs, no model."""

    def project(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(FEATURE)
        return project

    def test_a_new_test_that_cannot_import_on_base_proves_a_feature(self):
        result = self.project().verify(new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertIn("test_calc.CalcTests.test_c2_subtracts", result["fail_to_pass"])

    def test_the_same_change_is_not_a_bug_reproduction(self):
        result = self.project().verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail to import or collect" in reason for reason in result["failures"]), result["failures"])


class FeatureProofTests(unittest.TestCase):
    def prove(self, criteria, files=FEATURE):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(files)
        run_dir = Path(tempfile.mkdtemp(prefix="feature-proof-"))
        return regression.prove(feature_state(project, criteria), project.root, run_dir)

    def test_every_example_has_its_passing_test(self):
        proof = self.prove([ORDINARY, EXAMPLE])
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({"C2": ["test_calc.CalcTests.test_c2_subtracts"]}, proof["case_tests"])

    def test_an_example_without_its_test_fails_the_proof_and_is_named(self):
        missing = {**EXAMPLE, "id": "C3", "criterion": "Given calc.mul; when mul(2, 3) runs; then it returns 6",
                   "verification_method": "test: test_c3_multiplies"}
        proof = self.prove([EXAMPLE, missing])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["C3"])
        self.assertTrue(any("C3: Given calc.mul" in failure and "test_c3_" in failure for failure in proof["failures"]))


class PromptTests(unittest.TestCase):
    def test_planning_stages_get_the_rule_and_requirements_does_not(self):
        from tests.test_bug_job import SmallCorrectionTests
        from units import autoplanner
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage in ("astra_discovery", "astra_finalize"):
            self.assertIn(autoplanner.EXAMPLE_CRITERIA_RULE, autoplanner.context(state, stage, state_path)[0], stage)
        self.assertNotIn(autoplanner.EXAMPLE_CRITERIA_RULE,
                         autoplanner.context(state, "requirements_gather", state_path)[0])

    def test_the_builder_is_told_to_write_the_named_tests(self):
        from tests.test_bug_job import SmallCorrectionTests
        from units import common
        state = SmallCorrectionTests.start(SmallCorrectionTests())
        schemas = Path(test_cases.__file__).with_name("autocode-schemas")
        state_path = Path(state["workspace"]) / "state.json"
        self.assertNotIn("TESTS NAMED IN THE PLAN", common.execution_request(state, "terra", state_path, schemas).prompt)
        with patch.object(test_cases, "contract_cases", return_value=[{"id": "C2", "text": "x"}]):
            prompt = common.execution_request(state, "terra", state_path, schemas).prompt
        self.assertIn("TESTS NAMED IN THE PLAN", prompt.split("\nCURRENT HANDOFF DATA\n")[0])


if __name__ == "__main__":
    unittest.main()
