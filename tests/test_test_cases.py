"""Small features: acceptance criteria written as English examples and proven by the runner.

The approved one-milestone plan marks a criterion ``verification_method: "test: test_c2_..."``;
the runner's proof then requires that test to pass with the change and not without it.
See autocode_test_cases and docs/workflow.md.
"""
import json
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
    def test_annotated_go_name_binds_only_the_declared_actual_test(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [{
            "id": "AC1", "criterion": "golden behavior", "verification_method":
            "test: test_c1_golden_vectors — Go test TestC1GoldenVectors in policy_test.go"}]}}}
        cases = test_cases.contract_cases(state)
        actual = "policy::TestC1GoldenVectors"
        names = [actual, "policy::TestC10GoldenVectors", "policy::TestC1GoldenVectorsExtra"]
        self.assertEqual({"AC1": [actual]}, test_cases.match_cases(cases, names, framework="go"))
        self.assertEqual({"AC1": []}, test_cases.match_cases(cases, names, framework="pytest"))
        for passing, expected in (([actual], "PASS"), (names[1:], "FAIL"), ([], "FAIL")):
            with self.subTest(passing=passing):
                proof = {"framework": {"name": "go"}, "verdict": "PASS", "failures": [],
                         "unverified": [], "fail_to_pass": passing}
                regression.check_cases(proof, cases)
                self.assertEqual(expected, proof["verdict"])
        proof = {"framework": {"name": "go"}, "verdict": "PASS", "failures": [],
                 "unverified": [], "fail_to_pass": [actual]}
        regression.check_cases(proof, cases, refused={actual: "assertion does not test the behavior"})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertIn("assertion does not test the behavior", " ".join(proof["failures"]))

    def test_native_go_declaration_and_python_case_sensitive_name_are_preserved(self):
        self.assertEqual("TestC1GoldenVectors", test_cases.declared_test_name("TestC1GoldenVectors"))
        self.assertEqual("test_C1_vectors", test_cases.declared_test_name("test_C1_vectors — explanation"))
        self.assertEqual({"AC1": []}, test_cases.match_cases(
            [{"id": "AC1", "test_name": "test_C1_vectors"}], ["test_c1_vectors"], framework="pytest"))
        self.assertIsNone(test_cases.declared_test_name("test_c1_<what it checks>"))

    def test_approved_test_name_can_prove_two_criteria_with_one_focused_test(self):
        criteria = [
            {"id": "AC1", "criterion": "add returns five", "verification_method": "test: test_adds_two_integers"},
            {"id": "AC2", "criterion": "the focused suite passes", "verification_method": "test: test_adds_two_integers"},
        ]
        state = {"goal_contract": {"body": {"acceptance_criteria": criteria}}}
        cases = test_cases.contract_cases(state)
        ids = ["test_add.TestAdd.test_adds_two_integers"]
        self.assertEqual({"AC1": ids, "AC2": ids}, test_cases.match_cases(cases, ids))
        proof = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": ids}
        regression.check_cases(proof, cases)
        self.assertEqual("PASS", proof["verdict"])
        self.assertEqual([], proof["failures"])
        self.assertEqual({"AC1": [], "AC2": []},
                         test_cases.match_cases(cases, ["test_add.TestAdd.test_adds_two_integers_extra"]))
        missing = {"verdict": "PASS", "failures": [], "unverified": [], "fail_to_pass": []}
        regression.check_cases(missing, cases)
        self.assertEqual("FAIL", missing["verdict"])
        self.assertTrue(all("test_adds_two_integers" in failure for failure in missing["failures"]))

    def test_only_criteria_marked_test_are_cases(self):
        state = {"goal_contract": {"body": {"milestones": [{"id": "M1"}], "acceptance_criteria": [
            ORDINARY, EXAMPLE, {**EXAMPLE, "id": "C3", "verification_method": "  TEST: test_c3_x"}]}}}
        self.assertEqual([{"id": "C2", "text": EXAMPLE["criterion"], "test_name": "test_c2_subtracts"},
                          {"id": "C3", "text": EXAMPLE["criterion"], "test_name": "test_c3_x"}],
                         test_cases.contract_cases(state))
        self.assertEqual("C2: " + EXAMPLE["criterion"], test_cases.case_text(test_cases.contract_cases(state)[0]))

    def test_a_guard_criterion_is_a_preserve_case(self):
        guard = {**EXAMPLE, "id": "C4", "verification_method": "guard: test_c4_adds_still"}
        state = {"goal_contract": {"body": {"milestones": [{"id": "M1"}], "acceptance_criteria": [EXAMPLE, guard]}}}
        self.assertEqual([{"id": "C2", "text": EXAMPLE["criterion"], "test_name": "test_c2_subtracts"},
                          {"id": "C4", "text": EXAMPLE["criterion"], "test_name": "test_c4_adds_still", "kind": "preserve"}],
                         test_cases.contract_cases(state))
        self.assertIn('"guard:"', test_cases.builder_note(state))

    def test_an_approved_revision_may_switch_test_and_guard_but_keeps_the_same_proof(self):
        # A live review-then-fix plan (2026-09-29) could not move a criterion from test: to guard:, as its
        # Plan Reviewer asked, without asking the user.
        import autocode_goals as goals

        def revise(method, text=EXAMPLE["criterion"]):
            state = {"goal_contract": {"body": {"acceptance_criteria": [EXAMPLE]}, "approval_status": "approved"},
                     "answers": {}, "user_events": [], "brief_feedback": []}
            body = {"acceptance_criteria": [{**EXAMPLE, "criterion": text, "verification_method": method}]}
            goals.revision_guard(state, body, [], "glm_revise")

        revise("guard: test_c2_subtracts")
        revise("  GUARD:  test_c2_subtracts")
        for method, text in (("guard: test_c2_other", EXAMPLE["criterion"]), ("Validator reads calc.py", EXAMPLE["criterion"]),
                             ("guard: test_c2_subtracts", "Given calc.sub; when sub(5, 3) runs; then it returns 3")):
            with self.subTest(method=method, text=text), self.assertRaisesRegex(ValueError, "without a user-backed"):
                revise(method, text)

    def test_no_plan_no_cases(self):
        self.assertEqual([], test_cases.contract_cases({}))
        self.assertEqual("", test_cases.builder_note({}))

    def test_a_bug_fix_builder_is_told_to_test_through_code_that_exists_before_the_fix(self):
        # Issue #299: tests using a seam the fix added could not build on the unfixed code.
        def note(kind, criteria=(EXAMPLE,)):
            body = {"task_kind": kind, "milestones": [{"id": "M1"}], "acceptance_criteria": list(criteria)}
            return " ".join(test_cases.builder_note({"goal_contract": {"body": body}}).split())
        for criteria in ((EXAMPLE,), ()):
            with self.subTest(criteria=criteria):
                fix = note("bugfix", criteria)
                self.assertIn("Do not make a test import or reference anything the fix adds", fix)
                self.assertIn("Drive the real failure path through public APIs that exist before the fix", fix)
                self.assertIn("A log line or message alone does not prove the behavior", fix)
                # A guard keeps its own rule: it must pass before the fix, not fail there.
                self.assertIn("a guard: (preserve) test must pass there and after the fix", fix)
        self.assertIn("TESTS NAMED IN THE PLAN", note("bugfix"))
        self.assertNotIn("BUG FIX TESTS", note("build"))

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
        with patch.object(regression.util, "snapshot", return_value={"revision": "rev"}), \
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
        artifact = json.loads(Path(proof["path"]).read_text())
        self.assertEqual("PASS", artifact["verdict"])
        self.assertEqual(proof["case_tests"], artifact["case_tests"])
        self.assertEqual(["C2"], artifact["case_scope"])

    def test_an_example_without_its_test_fails_the_proof_and_is_named(self):
        missing = {**EXAMPLE, "id": "C3", "criterion": "Given calc.mul; when mul(2, 3) runs; then it returns 6",
                   "verification_method": "test: test_c3_multiplies"}
        proof = self.prove([EXAMPLE, missing])
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual([], proof["case_tests"]["C3"])
        self.assertTrue(any("C3: Given calc.mul" in failure and "test_c3_" in failure for failure in proof["failures"]))
        artifact = json.loads(Path(proof["path"]).read_text())
        self.assertEqual("FAIL", artifact["verdict"])
        self.assertEqual(proof["failures"], artifact["failures"])
        self.assertEqual([], artifact["case_tests"]["C3"])
        self.assertEqual(["C2", "C3"], artifact["case_scope"])


    # Live review-then-fix plans (2026-09-29) could only mark "a timeout before execution is still
    # retried" as test:, which the change never broke; the proof refused it or the plan asked the user.
    GUARD = {"id": "C4", "criterion": "Given calc.add; when add(1, 2) runs; then it still returns 3",
             "verification_method": "guard: test_c4_add_still_works", "human_review": False}
    GUARD_TEST = "\n    def test_c4_add_still_works(self):\n        self.assertEqual(3, add(1, 2))\n"
    OWN_FILE = "import unittest\nfrom calc import add\n\n\nclass GuardTests(unittest.TestCase):" + GUARD_TEST

    def test_a_parenthetical_go_name_is_the_test_the_proof_matches(self):
        method = ("test: TestAgentDomainStartPendingModDelayPreFlightAutomatonESSlices "
                  "(go test ./rule/preflight/ -run TestAgentDomainStartPendingModDelayPreFlightAutomatonESSlices)")
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "AC1", "criterion": "external nameservers relay", "verification_method": method}]}}}
        cases = test_cases.contract_cases(state)
        self.assertEqual("TestAgentDomainStartPendingModDelayPreFlightAutomatonESSlices", cases[0]["test_name"])
        self.assertNotIn("kind", cases[0])
        hyphenated = ("test: TestAgentDomainStartPendingModDelayPreFlightImpl/"
                      "Run_-_creates_missing_internal_zone_and_stages_status_4 "
                      "(go test ./rule/preflight/ -run 'TestAgentDomainStartPendingModDelayPreFlightImpl/"
                      "Run_-_creates_missing_internal_zone_and_stages_status_4' -v)")
        named = test_cases.contract_cases({"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "AC3", "criterion": "creates the zone", "verification_method": hyphenated}]}}})
        self.assertEqual(
            "TestAgentDomainStartPendingModDelayPreFlightImpl/Run_-_creates_missing_internal_zone_and_stages_status_4",
            named[0]["test_name"])

    def test_guard_only_coverage_passes_when_only_the_test_file_changes(self):
        proof = self.prove([self.GUARD], {"test_guard.py": self.OWN_FILE})
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_guard.GuardTests.test_c4_add_still_works"], proof["case_tests"]["C4"])

    def test_a_granted_test_only_exception_proves_coverage_marked_as_test(self):
        import autocode_contract_identity as identity
        import autocode_util as util
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write({"test_guard.py": self.OWN_FILE})
        criterion = {**self.GUARD, "verification_method": "test: test_c4_add_still_works"}
        state = feature_state(project, [criterion])
        contract = state["goal_contract"]
        contract.update(task_id="t", revision=2)
        contract["hash"] = util.digest({"task_id": "t", "revision": 2, "body": contract["body"]})
        state["answers"] = {"q": {
            "kind": "permission_answer", "contract_token": identity.token(contract),
            "text": "Grant a scoped test-only regression-proof exception for this goal.",
        }}
        proof = regression.prove(state, project.root, Path(tempfile.mkdtemp(prefix="coverage-exception-")))
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_guard.GuardTests.test_c4_add_still_works"], proof["case_tests"]["C4"])

    def test_a_guard_passes_with_a_test_that_passes_before_and_after(self):
        proof = self.prove([EXAMPLE, self.GUARD], {**FEATURE, "test_guard.py": self.OWN_FILE})
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_guard.GuardTests.test_c4_add_still_works"], proof["case_tests"]["C4"])

    def test_a_guard_that_could_not_run_before_counts_with_a_note(self):
        # Its file imports sub, which the change adds, so on the original code the file does not import.
        proof = self.prove([EXAMPLE, self.GUARD], {**FEATURE, "test_calc.py": FEATURE["test_calc.py"] + self.GUARD_TEST})
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_calc.CalcTests.test_c4_add_still_works"], proof["case_tests"]["C4"])
        self.assertTrue(any("not shown to have passed before" in note for note in proof["notes"]), proof["notes"])
        self.assertEqual(proof["notes"], json.loads(Path(proof["path"]).read_text())["notes"])

    def test_a_guard_whose_test_ran_and_failed_before_the_change_is_mis_tagged(self):
        new_behavior = {**self.GUARD, "id": "C5", "verification_method": "guard: test_c5_sub_exists"}
        test = ("import unittest\nimport calc\n\n\nclass MoreTests(unittest.TestCase):\n"
                "    def test_c5_sub_exists(self):\n        self.assertEqual(2, calc.sub(5, 3))\n")
        proof = self.prove([EXAMPLE, new_behavior], {**FEATURE, "test_more.py": test})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertTrue(any("fails on the original code" in failure for failure in proof["failures"]), proof["failures"])

    def prove_unchanged(self, criteria, seed):
        project = Project(seed)
        self.addCleanup(project.close)
        return regression.prove(feature_state(project, criteria), project.root,
                                Path(tempfile.mkdtemp(prefix="unchanged-proof-")))

    # A program re-checks a merged workstream after an accepted interface change: its files already
    # conform, so its validation-only plan marks every criterion guard: and changes nothing. A live
    # skeleton re-check (2026-10-06) stopped on "No change" with its guard tests passing.
    def test_a_guard_only_check_of_unchanged_source_passes_when_its_tests_hold(self):
        proof = self.prove_unchanged([ORDINARY, self.GUARD], {**SEED, "test_guard.py": self.OWN_FILE})
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({"C4": ["test_guard.GuardTests.test_c4_add_still_works"]}, proof["case_tests"])
        self.assertEqual([], proof["fail_to_pass"])
        self.assertEqual({"suite_on_candidate"}, set(proof["checks"]))
        state = {"regression_proof": proof, "goal_contract": {"body": {"acceptance_criteria": [self.GUARD]}}}
        self.assertTrue(regression.complete(state, proof["source_revision"]))

    def test_an_unchanged_source_still_fails_a_guard_without_its_test(self):
        proof = self.prove_unchanged([self.GUARD], SEED)
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"C4": []}, proof["case_tests"])
        self.assertTrue(any("C4" in failure and "test_c4_add_still_works" in failure
                            for failure in proof["failures"]), proof["failures"])
        self.assertFalse(any("No change" in failure for failure in proof["failures"]), proof["failures"])

    def test_an_unchanged_source_fails_a_guard_whose_test_fails(self):
        broken = self.OWN_FILE.replace("self.assertEqual(3, add(1, 2))", "self.assertEqual(4, add(1, 2))")
        proof = self.prove_unchanged([self.GUARD], {**SEED, "test_guard.py": broken})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"C4": []}, proof["case_tests"])
        self.assertTrue(any("passes both with the change and on the original code" in failure
                            for failure in proof["failures"]), proof["failures"])

    def test_an_unchanged_source_never_proves_new_behavior(self):
        proof = self.prove_unchanged([EXAMPLE, self.GUARD], {**FEATURE, "test_guard.py": self.OWN_FILE})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertIn("No change: the candidate is identical to the base revision", proof["failures"])


class BaseAndTimeoutTests(unittest.TestCase):
    """Runs created before base_commit was saved, and suites that outlast the default timeout."""

    def recorded(self, project, head):
        snapshot = Path(tempfile.mkdtemp(prefix="first-stage-")) / "before.json"
        snapshot.write_text(json.dumps({"head": head, "files": {}, "revision": "r"}))
        state = feature_state(project, [ORDINARY, EXAMPLE])
        del state["base_commit"]
        state["stages"] = [{"stage": "requirements_gather", "before_ref": str(snapshot)}]
        return state

    def test_a_run_without_a_saved_base_is_proven_against_the_head_its_first_stage_recorded(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        project.write(FEATURE)
        proof = regression.prove(self.recorded(project, project.base), project.root,
                                 Path(tempfile.mkdtemp(prefix="feature-proof-")))
        self.assertEqual((project.base, "PASS"), (proof["base"], proof["verdict"]), proof["unverified"])

    def test_a_recorded_head_the_source_does_not_descend_from_is_not_a_base(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        self.assertIsNone(regression.base_commit(self.recorded(project, "0" * 40), project.root))
        self.assertIsNone(regression.base_commit({"stages": []}, project.root))

    def test_the_suite_timeout_follows_the_runs_tool_limit(self):
        def timeout(regression_settings=None, **limits):
            return regression.suite_timeout({"settings": {"regression": regression_settings, "limits": limits}})
        self.assertEqual(3600, timeout(tool_timeout_seconds=3600))
        self.assertIsNone(timeout(tool_timeout_seconds=0))
        self.assertEqual(verify.DEFAULT_TIMEOUT, timeout(tool_timeout_seconds=60))
        self.assertEqual(verify.DEFAULT_TIMEOUT, timeout())
        self.assertEqual(7200, timeout({"test_timeout": 7200}, tool_timeout_seconds=0))


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

    def test_the_plan_reviewer_recomputes_worked_examples(self):
        from tests.test_bug_job import SmallCorrectionTests
        from units import autoplanner
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage, wanted in (("astra_challenge", True), ("astra_finalize", True), ("astra_discovery", False),
                              ("glm_revise", False)):
            with self.subTest(stage=stage):
                prompt = autoplanner.context(state, stage, state_path)[0]
                self.assertEqual(wanted, "CHECK EVERY WORKED EXAMPLE" in prompt)

    def test_the_plan_reviewer_checks_every_example_against_the_brief(self):
        # A live greenfield run (2026-10-01) transcribed the brief's "ID TEXT [open|done]" into examples
        # without the brackets and everything downstream honestly served the corrupted criteria.
        from tests.test_bug_job import SmallCorrectionTests
        from units import autoplanner
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage, wanted in (("astra_challenge", True), ("astra_finalize", True), ("astra_discovery", False),
                              ("glm_revise", False)):
            with self.subTest(stage=stage):
                prompt = autoplanner.context(state, stage, state_path)[0]
                self.assertEqual(wanted, "CHECK EVERY EXAMPLE AGAINST THE BRIEF" in prompt)

    def test_every_planning_stage_forbids_timing_criteria_except_requirements(self):
        from tests.test_bug_job import SmallCorrectionTests
        from units import autoplanner
        state = SmallCorrectionTests.start(SmallCorrectionTests(), fix_size="large")
        state["settings"]["roles"]["plan_reviewer"] = {"model": "p"}
        state_path = Path(state["workspace"]) / "state.json"
        for stage, wanted in (("astra_discovery", True), ("astra_challenge", True), ("glm_revise", True),
                              ("astra_finalize", True), ("requirements_gather", False)):
            with self.subTest(stage=stage):
                self.assertEqual(wanted, "NO TIMING CRITERIA" in autoplanner.context(state, stage, state_path)[0])

    def test_the_builder_is_told_to_write_the_named_tests(self):
        from tests.test_bug_job import approved_small_fix
        from units import common
        state = approved_small_fix()
        schemas = Path(test_cases.__file__).with_name("autocode-schemas")
        state_path = Path(state["workspace"]) / "state.json"
        prompt = common.execution_request(state, "terra", state_path, schemas).prompt
        self.assertNotIn("TESTS NAMED IN THE PLAN", prompt)
        self.assertIn("TESTS NAMED IN THE DIAGNOSIS", prompt.split("\nCURRENT HANDOFF DATA\n")[0])
        self.assertIn("BUG FIX TESTS", prompt.split("\nCURRENT HANDOFF DATA\n")[0])
        state.pop("investigation")
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


class DiagnosisCaseBuilderTests(unittest.TestCase):
    """The live Boltons fix used AC names, leaving the runner's T1–T6 cases unproven."""

    CASES = [
        {"id": "T1", "given": "IndexedSet([1]) and three lists", "when": "update receives the lists",
         "then": "members are [1, 2, 3, 4] and the return value is None"},
        {"id": "T2", "given": "IndexedSet([1]) and two tuples", "when": "update receives the tuples",
         "then": "members are [1, 2, 3, 4] and the return value is None", "kind": "restore"},
        {"id": "T3", "given": "IndexedSet([1]) and two one-shot iterators", "when": "update receives the iterators",
         "then": "members are [1, 2, 3, 4] and both iterators are exhausted", "kind": "restore"},
        {"id": "T4", "given": "iterables yielding tuple-valued members", "when": "update receives the iterables",
         "then": "each tuple remains one member", "kind": "restore"},
        {"id": "T5", "given": "IndexedSet([1])", "when": "update receives no arguments",
         "then": "members remain [1] and the return value is None", "kind": "preserve"},
        {"id": "T6", "given": "IndexedSet([1]) and [2, 1, 3]", "when": "update receives one iterable",
         "then": "members are [1, 2, 3] and the return value is None", "kind": "preserve"},
    ]

    def test_the_actual_builder_prompt_lists_every_diagnosis_case_and_its_proof(self):
        from tests.test_bug_job import approved_small_fix
        from units import common
        state = approved_small_fix(test_cases=self.CASES)
        schemas = Path(test_cases.__file__).with_name("autocode-schemas")
        prompt = common.execution_request(state, "terra", Path(state["workspace"]) / "state.json", schemas).prompt
        note = prompt.split("\nCURRENT HANDOFF DATA\n")[0]
        self.assertIn("TESTS NAMED IN THE DIAGNOSIS", note)
        self.assertIn("one separate test for each Investigator case", note)
        self.assertIn("NAMED TEST PROOF", note)
        self.assertIn("BUG FIX TESTS", note)
        for case in self.CASES:
            with self.subTest(case=case["id"]):
                row = next(line for line in note.splitlines() if line.startswith("- " + case["id"] + ":"))
                self.assertIn(test_cases.case_text(case), row)
                self.assertIn("test_" + case["id"].lower() + "_<what it checks>", row)
                self.assertIn("must pass on the original code and with the fix" if case.get("kind") == "preserve"
                              else "must fail on the original code because of the bug and pass with the fix", row)

    def test_diagnosis_names_take_precedence_over_the_plans_named_criteria(self):
        state = {"investigation": {"outcome": "reproduced", "test_cases": [T1, T4]},
                 "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [
                     {"id": "AC1", "criterion": "partial pages", "verification_method": "test: test_ac1_pages"}]}}}
        note = test_cases.builder_note(state)
        self.assertIn("test_t1_<what it checks>", note)
        self.assertIn("test_t4_<what it checks>", note)
        self.assertNotIn("TESTS NAMED IN THE PLAN", note)
        self.assertEqual([T1, T4], regression.cases(state))

    def test_a_real_proof_still_rejects_plan_names_for_diagnosis_cases(self):
        project = Project(BUG_SEED)
        self.addCleanup(project.close)
        project.write({**BUG_FIX, "test_pager.py": BUG_FIX["test_pager.py"]
                       .replace("test_t1_partial", "test_ac1_partial").replace("test_t4_exact", "test_ac4_exact")})
        state = {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [
                     {"id": "AC1", "criterion": "partial pages", "verification_method": "test: test_ac1_partial_page_counts"}],
                     "milestones": [{"id": "M1"}]}},
                 "investigation": {"outcome": "reproduced", "test_cases": [T1, T4]}}
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual("FAIL", proof["verdict"])
        self.assertEqual({"T1": [], "T4": []}, proof["case_tests"])
        self.assertIn("test_pager.PagerTests.test_ac1_partial_page_counts", proof["fail_to_pass"])
        self.assertIn("test_pager.PagerTests.test_ac4_exact_multiple_and_zero", proof["pass_to_pass"])
        self.assertTrue(any("test_t1_" in reason for reason in proof["failures"]), proof["failures"])
        self.assertTrue(any("test_t4_" in reason for reason in proof["failures"]), proof["failures"])


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


class MixedCaseIdTests(unittest.TestCase):
    def test_documented_lowercase_names_match_ids_with_letters_after_digits(self):
        for cid in ("M1A", "M1C", "AC18", "Feature2A"):
            name = "test_" + cid.lower() + "_behavior"
            ids = ["tests.test_sample.Sample." + name,
                   "tests/test_sample.py::Sample::" + name + "[blue]", name]
            with self.subTest(case=cid):
                self.assertEqual({cid: ids}, test_cases.match_cases([{"id": cid}], ids))
        self.assertEqual({"T1": ["TestT1X"], "M1A": ["TestM1A_Behavior"]},
                         test_cases.match_cases([{"id": "T1"}, {"id": "M1A"}],
                                               ["TestT1X", "TestM1A_Behavior"]))

    def test_case_ids_still_require_whole_tokens_in_the_test_function(self):
        for cid, other in (("M1A", "m1alpha"), ("M1A", "m1a2"), ("T1", "t10"), ("AC18", "ac180")):
            names = ["test_" + other + "_behavior", "test_" + cid.lower() + ".Test" + cid + ".test_other",
                     "tests/test_sample.py::Test" + cid + "::test_other[" + cid + "]"]
            with self.subTest(case=cid, other=other):
                self.assertEqual({cid: []}, test_cases.match_cases([{"id": cid}], names))

    def test_explicit_approved_test_name_never_falls_back_to_case_id(self):
        case = {"id": "M1A", "test_name": "test_specific_behavior"}
        names = ["test_m1a_behavior", "test_specific_behavior_extra", "test_specific_behavior"]
        self.assertEqual({"M1A": ["test_specific_behavior"]}, test_cases.match_cases([case], names))

    def test_real_bug_proof_attributes_restore_and_preserve_mixed_ids(self):
        project = Project(BUG_SEED)
        self.addCleanup(project.close)
        project.write({**BUG_FIX, "test_pager.py": BUG_FIX["test_pager.py"]
                       .replace("test_t1_partial", "test_m1a_partial")
                       .replace("test_t4_exact", "test_m1c_exact")})
        state = {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [],
                                            "milestones": [{"id": "M1"}]}},
                 "investigation": {"outcome": "reproduced", "test_cases":
                                   [{**T1, "id": "M1A"}, {**T4, "id": "M1C"}]}}
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_pager.PagerTests.test_m1a_partial_page_counts"], proof["case_tests"]["M1A"])
        self.assertEqual(["test_pager.PagerTests.test_m1c_exact_multiple_and_zero"], proof["case_tests"]["M1C"])
