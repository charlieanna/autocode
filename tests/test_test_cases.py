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


BUG_SEED = {"pager.py": "def page_count(total, size):\n    return total // size\n",
            "test_pager.py": "import unittest\nfrom pager import page_count\n\n\nclass PagerTests(unittest.TestCase):\n"
                             "    def test_existing(self):\n        self.assertEqual(2, page_count(10, 5))\n"}
BUG_FIX = {"pager.py": "def page_count(total, size):\n    return (total + size - 1) // size\n",
           "test_pager.py": BUG_SEED["test_pager.py"]
           + "\n    def test_t1_partial_page_counts(self):\n        self.assertEqual(3, page_count(11, 5))\n"
           + "\n    def test_t4_exact_multiple_and_zero(self):\n"
             "        self.assertEqual(2, page_count(10, 5))\n        self.assertEqual(0, page_count(0, 5))\n"}
T1 = {"id": "T1", "given": "total=11, size=5", "when": "page_count(11, 5)", "then": "returns 3"}
T4 = {"id": "T4", "given": "total=10 and total=0, size=5", "when": "page_count runs", "then": "returns 2 and 0",
      "kind": "preserve"}


class PreserveCaseProofTests(unittest.TestCase):
    """A preserve case (#129) is proven by the runner's own test runs, not a hand-built proof."""

    def prove(self, cases):
        project = Project(BUG_SEED)
        self.addCleanup(project.close)
        project.write(BUG_FIX)
        state = {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [],
                                            "milestones": [{"id": "M1"}]}},
                 "investigation": {"outcome": "reproduced", "test_cases": cases}}
        return regression.prove(state, project.root, Path(tempfile.mkdtemp(prefix="preserve-proof-")))

    def test_a_preserve_case_is_proven_by_a_test_that_passes_before_and_after_the_fix(self):
        proof = self.prove([T1, T4])
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_pager.PagerTests.test_t1_partial_page_counts"], proof["case_tests"]["T1"])
        self.assertEqual(["test_pager.PagerTests.test_t4_exact_multiple_and_zero"], proof["case_tests"]["T4"])

    def test_the_same_case_as_a_restore_case_still_has_to_fail_first(self):
        proof = self.prove([T1, {**T4, "kind": "restore"}])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["T4"])


class DesignOnlyTests(unittest.TestCase):
    """A design job delivers documents: a live one planned every criterion as a test and added tests/."""

    def state(self, kind):
        return {"workflow": {"kind": kind}, "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
                "goal_contract": {"body": {"task_kind": "build", "milestones": [{"id": "M1"}],
                                           "acceptance_criteria": [EXAMPLE]}}}

    def test_a_design_job_has_no_test_cases_and_needs_no_proof(self):
        design = self.state("design")
        self.assertTrue(test_cases.design_only(design))
        self.assertEqual([], test_cases.contract_cases(design))
        self.assertEqual("", test_cases.builder_note(design))
        self.assertFalse(regression.required(design))
        build = self.state("build")
        self.assertFalse(test_cases.design_only(build))
        self.assertEqual(["C2"], [case["id"] for case in test_cases.contract_cases(build)])
        self.assertTrue(regression.required(build))

    def test_the_planner_gets_the_design_rule_instead_of_the_test_rule(self):
        from units import autoplanner
        from tests.test_bug_job import state_for
        for kind, present, absent in (("design", autoplanner.DESIGN_DELIVERABLES_RULE, autoplanner.EXAMPLE_CRITERIA_RULE),
                                      ("build", autoplanner.EXAMPLE_CRITERIA_RULE, autoplanner.DESIGN_DELIVERABLES_RULE)):
            state = {**state_for(), "workflow": {"kind": kind}, "answers": {}, "user_events": []}
            state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
            prompt, _ = autoplanner.context(state, "astra_discovery", Path("/tmp/state.json"))
            with self.subTest(kind=kind):
                self.assertIn(present, prompt)
                self.assertNotIn(absent, prompt)


class FileListingRuleTests(unittest.TestCase):
    """A live Go port's plan made "deliver these four files" a test that listed the repository root;
    it failed on correct code once the scenario's checker built a binary there (2026-09-29)."""

    def test_build_plans_are_told_to_test_behavior_not_the_file_listing(self):
        from units import autoplanner
        from tests.test_bug_job import state_for
        state = {**state_for(), "workflow": {"kind": "build"}, "answers": {}, "user_events": []}
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        prompt, _ = autoplanner.context(state, "astra_discovery", Path("/tmp/state.json"))
        rule = "A test checks what the program does, never which files the repository contains."
        self.assertIn(rule, autoplanner.EXAMPLE_CRITERIA_RULE)
        self.assertIn(rule, prompt)
        self.assertIn('checked by the Validator reading the\nrepository', prompt)
