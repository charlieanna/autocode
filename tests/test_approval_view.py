"""What the plan approval stop shows: the decision summary and the limits line (issue #381)."""
import copy
import unittest

import autocode_approval_view as approval_view
import autocode_test_cases as test_cases
from goal_fixtures import body


class LimitsTests(unittest.TestCase):
    def test_absent_or_zero_limits_read_as_no_limit(self):
        settings = {"limits": {"max_seconds": 0, "stage_timeout_seconds": None, "iteration_ceiling": None},
                    "orchestration": {"enabled": False, "max_parallel": 4}}
        self.assertEqual("Limits in effect: no time limit for the run, no per-stage time limit, "
                         "no iteration ceiling, one Builder at a time.", approval_view.limits(settings, 3))
        self.assertEqual(approval_view.limits(settings, 3), approval_view.limits({}, 3))

    def test_set_limits_read_in_hours_minutes_or_seconds(self):
        settings = {"limits": {"max_seconds": 5400, "stage_timeout_seconds": 45, "iteration_ceiling": 0},
                    "orchestration": {"enabled": True, "max_parallel": 3}}
        self.assertEqual("Limits in effect: 90 min of active time for the run, 45 s per stage, "
                         "stops after iteration 0 (now at 0), up to 3 Builders at once.",
                         approval_view.limits(settings, 0))


def criterion(cid, text, method, human=False):
    return {"id": cid, "criterion": text, "verification_method": method, "human_review": human}


PROOF_HEAD = ("  - An independent check of the final source must pass every criterion above, and the runner itself "
              "re-runs that check's commands in a clean copy: each must exit 0.")
NOT_PROVEN = "  - Not proven: behavior no criterion describes, or inputs no check exercises."


class SummaryTests(unittest.TestCase):
    def test_every_section_quotes_the_plan_verbatim(self):
        plan = body()
        plan["milestones"].append({"id": "M2", "objective": "Document it", "acceptance_criteria": ["C2"]})
        plan["permission_boundaries"] = ["May edit only: greet.py, test_greeting.py",
                                         "Must not modify anything under .autocode/"]
        plan["acceptance_criteria"].append(criterion(
            "C2", "`greet.py Ada` prints exactly \"Hello, Ada\" and exits 0", "test: test_c2_greets_ada"))
        self.assertEqual([
            "Before you approve r7 (a summary of the plan above):",
            "What it will do:",
            "  Provide a deterministic greeting CLI",
            "Built in 2 milestones: M1, M2.",
            "What it may change:",
            "  - May edit only: greet.py, test_greeting.py",
            "  - Must not modify anything under .autocode/",
            "Out of scope:",
            "  - Web service",
            "  - Deployment",
            "Done when:",
            "  [C1] Contract holds",
            "    Checked by: Execute greeting and invalid-input regression checks",
            "  [C2] `greet.py Ada` prints exactly \"Hello, Ada\" and exits 0",
            "    Checked by: test: test_c2_greets_ada",
            "What passing proves:",
            PROOF_HEAD,
            "  - The runner also runs the test each of these criteria names: C2 (test:) must pass with the change "
            "and not without it. No test that passed before may fail now.",
            NOT_PROVEN,
        ], approval_view.summary(plan, 7))

    def test_a_field_an_older_plan_lacks_is_left_out(self):
        self.assertEqual(["Before you approve r2 (a summary of the plan above):"], approval_view.summary({}, 2))
        old = {"intended_outcome": "  ", "milestones": "M1", "permission_boundaries": None,
               "acceptance_criteria": [{"id": "AC1"}, "not a row", {"criterion": "Unnamed"}]}
        self.assertEqual(["Before you approve r3 (a summary of the plan above):",
                          "Done when:", "  [AC1]", "  Unnamed",
                          "What passing proves:", PROOF_HEAD,
                          "  - No criterion is marked test: or guard:, so nothing shows that a check would fail "
                          "without the change.", NOT_PROVEN],
                         approval_view.summary(old, 3))
        empty = {"permission_boundaries": [], "scope_exclusions": [], "acceptance_criteria": []}
        self.assertEqual(["Before you approve r1 (a summary of the plan above):", "What it may change:",
                          "  (none declared)", "Out of scope:", "  (none declared)"],
                         approval_view.summary(empty, 1))

    def test_every_permission_is_shown_but_long_scope_is_capped(self):
        plan = {"permission_boundaries": [f"Boundary {n}" for n in range(1, 9)],
                "scope_exclusions": [f"Exclusion {n}" for n in range(1, 7)]}
        shown = approval_view.summary(plan, 1)
        self.assertEqual(["  - Boundary " + str(n) for n in range(1, 9)], shown[2:10])
        self.assertEqual(["Out of scope:", "  - Exclusion 1", "  - Exclusion 2", "  - Exclusion 3",
                          "  - ... and 3 more in the plan above"], shown[10:])
        plan["scope_exclusions"] = plan["scope_exclusions"][:3]
        self.assertEqual(["Out of scope:", "  - Exclusion 1", "  - Exclusion 2", "  - Exclusion 3"],
                         approval_view.summary(plan, 1)[10:])

    def test_a_criterion_for_human_review_says_it_also_needs_yours(self):
        plan = {"acceptance_criteria": [criterion("C1", "Looks right", "Inspect the page", human=True),
                                        criterion("C2", "Works", "Run it")]}
        shown = approval_view.summary(plan, 1)
        self.assertEqual(["  [C1] Looks right", "    Checked by: Inspect the page",
                          "    Also needs your review of the result before the run can complete.",
                          "  [C2] Works", "    Checked by: Run it", "What passing proves:"], shown[2:8])
        self.assertEqual(1, sum("needs your review" in line for line in shown[2:7]))
        self.assertEqual("  - An independent check of the final source must pass every criterion above (it may leave "
                         "those marked for your review to you), and the runner itself re-runs that check's commands "
                         "in a clean copy: each must exit 0.", shown[8])

    def test_what_passing_proves_follows_the_job_and_how_each_criterion_is_checked(self):
        named = [criterion("C1", "New", "test: test_c1_new"), criterion("C2", "Kept", "Guard: test_c2_kept"),
                 criterion("C3", "Also new", " TEST: test_c3_also_new"), criterion("C4", "Prose", "Read the diff")]
        build = {"task_kind": "build", "acceptance_criteria": named}
        self.assertEqual(
            "  - The runner also runs the test each of these criteria names: C1, C3 (test:) must pass with the change "
            "and not without it; C2 (guard:) must pass with the change and must not fail without it. No test that "
            "passed before may fail now.", approval_view.summary(build, 1)[-2])
        bugfix = {**copy.deepcopy(build), "task_kind": "bugfix"}
        self.assertEqual(
            "  - Bug fix: the runner also checks that a new or changed test fails on the original code and passes "
            "with the fix, and that no test that passed before now fails.", approval_view.summary(bugfix, 1)[-2])
        self.assertEqual(
            "  - Design job: no test named in a criterion is run as proof, so nothing shows that a check would fail "
            "without the change.", approval_view.summary(build, 1, design_only=True)[-2])
        prose = {"acceptance_criteria": [named[3]]}
        self.assertEqual(
            "  - No criterion is marked test: or guard:, so nothing shows that a check would fail without the change.",
            approval_view.summary(prose, 1)[-2])
        for plan in (build, bugfix, prose):
            shown = approval_view.summary(plan, 1)
            self.assertEqual(["What passing proves:", PROOF_HEAD], shown[-4:-2])
            self.assertEqual(NOT_PROVEN, shown[-1])

    def test_test_marks_are_the_ones_the_regression_proof_reads(self):
        self.assertEqual((test_cases.MARK, test_cases.GUARD_MARK), (approval_view.TEST_MARK, approval_view.GUARD_MARK))
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            criterion("C1", "New", "test: test_c1_new"), criterion("C2", "Kept", "Guard: test_c2_kept"),
            criterion("C3", "Prose", "Read the diff")]}}}
        self.assertEqual(["C1", "C2"], [case["id"] for case in test_cases.contract_cases(state)])
        self.assertIn("C1 (test:)", approval_view.summary(state["goal_contract"]["body"], 1)[-2])
        self.assertIn("C2 (guard:)", approval_view.summary(state["goal_contract"]["body"], 1)[-2])
        self.assertTrue(test_cases.design_only({"workflow": {"kind": "design"}}))


if __name__ == "__main__":
    unittest.main()
