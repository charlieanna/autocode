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

    def test_no_plan_no_cases(self):
        self.assertEqual([], test_cases.contract_cases({}))
        self.assertEqual("", test_cases.builder_note({}))

    def test_the_proof_is_required_only_when_the_plan_names_tests(self):
        project = type("Committed", (), {"base": "b"})()
        self.assertTrue(regression.required(feature_state(project, [ORDINARY, EXAMPLE])))
        self.assertFalse(regression.required(feature_state(project, [ORDINARY])))
        # A milestone whose tested criteria are all later ones runs no proof yet.
        early = feature_state(project, [EXAMPLE], milestones=2)
        early["goal_contract"]["body"]["milestones"][1]["acceptance_criteria"] = ["C2"]
        early["current_task"] = {"milestone_id": "M1"}
        self.assertFalse(regression.required(early))
        self.assertEqual("", test_cases.builder_note(early))


def planned(current, accepted=(), batch=None, hash_="h1"):
    """A three-milestone plan: M1 has C1 (test), M2 has C2 (test) and C4 (ordinary), M3 has C3 (test);
    C5 (test) belongs to no milestone."""
    criteria = [{"id": cid, "criterion": f"example {cid}", "verification_method": f"test: test_{cid.lower()}_x"}
                for cid in ("C1", "C2", "C3", "C5")] + [dict(ORDINARY, id="C4")]
    milestones = [{"id": "M1", "acceptance_criteria": ["C1"]}, {"id": "M2", "acceptance_criteria": ["C2", "C4"]},
                  {"id": "M3", "acceptance_criteria": ["C3"]}]
    progress = {f"{hash_}:{mid}": {"id": mid, "accepted": True, "contract_hash": hash_} for mid in accepted}
    task = {"milestone_ids": list(batch)} if batch else {"milestone_id": current}
    return {"goal_contract": {"hash": "h1", "body": {"milestones": milestones, "acceptance_criteria": criteria}},
            "current_task": task, "milestone_progress": progress}


class MilestoneScopeTests(unittest.TestCase):
    """A case is due once its milestone is current or accepted; unassigned ones at the end."""

    def ids(self, state):
        return [case["id"] for case in test_cases.contract_cases(state)]

    def test_the_first_milestone_proves_only_its_own_tests(self):
        self.assertEqual({"C1"}, test_cases.in_scope(planned("M1")))
        self.assertEqual(["C1"], self.ids(planned("M1")))

    def test_later_milestones_also_prove_the_accepted_ones(self):
        self.assertEqual(["C1", "C2"], self.ids(planned("M2", accepted=["M1"])))

    def test_acceptance_under_another_contract_does_not_count(self):
        self.assertEqual(["C2"], self.ids(planned("M2", accepted=["M1"], hash_="old")))

    def test_a_parallel_batch_proves_every_member(self):
        self.assertEqual(["C1", "C3"], self.ids(planned(None, batch=["M1", "M3"])))

    def test_everything_is_due_once_every_milestone_is_reached(self):
        state = planned("M3", accepted=["M1", "M2"])
        self.assertIsNone(test_cases.in_scope(state))
        self.assertEqual(["C1", "C2", "C3", "C5"], self.ids(state))

    def test_a_task_without_a_milestone_proves_everything(self):
        state = planned("M1")
        state["current_task"] = {"objective": "legacy task"}
        self.assertEqual(["C1", "C2", "C3", "C5"], self.ids(state))

    def test_a_proof_for_a_smaller_scope_is_not_reused(self):
        state = planned("M2", accepted=["M1"])
        state["regression_proof"] = {"verdict": "PASS", "source_revision": "rev", "case_scope": ["C1"]}
        with patch.object(regression.support, "snapshot", return_value={"revision": "rev"}), \
                patch.object(regression, "base_commit", return_value=None):
            proof = regression.prove(state, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()))
        self.assertEqual(["C1", "C2"], proof["case_scope"])


class TwoMilestoneProofTests(unittest.TestCase):
    """Real repositories: M1 delivers sub (C1), M2 delivers mul (C2)."""
    MUL = {"calc.py": FEATURE["calc.py"] + "\n\ndef mul(a, b):\n    return a * b\n",
           "test_calc.py": FEATURE["test_calc.py"].replace("test_c2_subtracts", "test_c1_subtracts")
           .replace("from calc import add, sub", "from calc import add, sub, mul")
           + "\n    def test_c2_multiplies(self):\n        self.assertEqual(6, mul(2, 3))\n"}

    def prove(self, files, current, accepted=()):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(files)
        criteria = [{**EXAMPLE, "id": "C1", "verification_method": "test: test_c1_subtracts"},
                    {**EXAMPLE, "id": "C2", "criterion": "Given calc.mul; when mul(2, 3) runs; then it returns 6",
                     "verification_method": "test: test_c2_multiplies"}]
        state = feature_state(project, criteria, milestones=2)
        state["goal_contract"]["hash"] = "h"
        state["goal_contract"]["body"]["milestones"] = [{"id": "M1", "acceptance_criteria": ["C1"]},
                                                        {"id": "M2", "acceptance_criteria": ["C2"]}]
        state["current_task"] = {"milestone_id": current}
        state["milestone_progress"] = {f"h:{mid}": {"id": mid, "accepted": True, "contract_hash": "h"}
                                       for mid in accepted}
        return regression.prove(state, project.root, Path(tempfile.mkdtemp(prefix="milestone-proof-")))

    def test_the_first_milestone_is_proven_without_the_second(self):
        m1 = {"calc.py": FEATURE["calc.py"], "test_calc.py": FEATURE["test_calc.py"].replace("test_c2_", "test_c1_")}
        proof = self.prove(m1, "M1")
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["C1"], sorted(proof["case_tests"]))

    def test_the_second_milestone_proves_both(self):
        proof = self.prove(self.MUL, "M2", accepted=["M1"])
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({"C1": ["test_calc.CalcTests.test_c1_subtracts"],
                          "C2": ["test_calc.CalcTests.test_c2_multiplies"]}, proof["case_tests"])

    def test_losing_an_accepted_milestones_test_fails_the_later_checkpoint(self):
        without_c1 = {**self.MUL, "test_calc.py": self.MUL["test_calc.py"].replace("test_c1_subtracts", "test_sub")}
        proof = self.prove(without_c1, "M2", accepted=["M1"])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["C1"])


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
        from tests.test_bug_job import approved_small_fix
        from units import common
        state = approved_small_fix()
        schemas = Path(test_cases.__file__).with_name("autocode-schemas")
        state_path = Path(state["workspace"]) / "state.json"
        self.assertNotIn("TESTS NAMED IN THE PLAN", common.execution_request(state, "terra", state_path, schemas).prompt)
        with patch.object(test_cases, "contract_cases", return_value=[{"id": "C2", "text": "x"}]):
            prompt = common.execution_request(state, "terra", state_path, schemas).prompt
        self.assertIn("TESTS NAMED IN THE PLAN", prompt.split("\nCURRENT HANDOFF DATA\n")[0])


if __name__ == "__main__":
    unittest.main()
