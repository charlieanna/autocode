"""The pending milestone replan and the Completion Owner's statement of it (issue #459). Pure logic."""

import copy
import unittest

import autocode_milestone_replan as replan
import autocode_milestones as milestones
import autocode_support as support
import autocode_util as util

LIMITS = {"enabled": True, "max_seconds": 5400, "stalled_reviews": 3, "max_replans": 1}
# The live run's stalled milestone (feature-stock-refusals, run 8soi9a5s).
STALLED = {"id": "M1", "needs_replan": True, "replans": 0, "reviews_without_progress": 4, "rejected_advances": 2}
# An integrated batch's row (autocode_milestone_scope.scope): its id is a digest no next_task.milestone_id can take.
BATCH = {**STALLED, "id": "batch:caa8bd913910f702", "milestone_ids": ["M1", "M2"], "reviews_without_progress": 3}


class PendingReplanTests(unittest.TestCase):
    def test_diagnosed_approach_failure_is_bounded_without_fabricating_validations(self):
        row = {
            "id": "M1",
            "replans": 0,
            "reviews_without_progress": 0,
            "builder_reassessment": {"failure_id": "f1", "evidence_refs": ["builder.json"]},
        }
        for cap in (None, 0, 1):
            limits = {**LIMITS, "max_replans": cap, "stalled_reviews": None}
            self.assertEqual(replan.REQUIRED, replan.pending(row, limits))
            expected = replan.EXHAUSTED if cap == 1 else replan.REQUIRED
            self.assertEqual(expected, replan.pending({**row, "replans": 1}, limits))
            self.assertIn("not independent validation", replan.constraint(row, limits))
            self.assertIn("replan 1 of 1" if cap == 1 else "Replans are unbounded", replan.constraint(row, limits))
        self.assertEqual(0, row["reviews_without_progress"])

    def test_a_stalled_milestone_with_replans_left_requires_a_changed_rework(self):
        self.assertEqual(replan.REQUIRED, replan.pending(STALLED, LIMITS))
        self.assertEqual(replan.REQUIRED, replan.pending(STALLED, {**LIMITS, "max_replans": None}))
        self.assertEqual(replan.REQUIRED, replan.pending({**STALLED, "replans": 5}, {**LIMITS, "max_replans": None}))

    def test_spent_replans_are_exhausted_not_required(self):
        self.assertEqual(replan.EXHAUSTED, replan.pending({**STALLED, "replans": 1}, LIMITS))

    def test_no_replan_without_a_stall_or_with_stall_detection_off(self):
        self.assertIsNone(replan.pending({**STALLED, "needs_replan": False}, LIMITS))
        self.assertIsNone(replan.pending(STALLED, {**LIMITS, "stalled_reviews": None}))
        self.assertIsNone(replan.pending(None, LIMITS))
        self.assertIsNone(replan.pending(STALLED, None))


class ReplanConstraintTests(unittest.TestCase):
    def test_states_the_gate_the_next_decision_must_pass(self):
        text = replan.constraint(STALLED, LIMITS)
        self.assertIn("MILESTONE REPLAN REQUIRED", text)
        self.assertIn("next task on M1 only with status REWORK, nonempty evidence and a changed approach", text)
        self.assertIn("A CONTINUE on M1, including a kind=validate revalidation, is refused", text)
        self.assertIn("PAUSED_MILESTONE_REPLAN", text)
        self.assertIn("return REWORK with\nnext_task.kind=validate", text)
        self.assertIn("4 validations without progress (limit 3)", text)
        self.assertIn("replan 1 of 1", text)
        self.assertIn("PAUSED_MILESTONE_STALLED", text)

    def test_unbounded_replans_still_name_the_changed_rework(self):
        text = replan.constraint({**STALLED, "replans": 2}, {**LIMITS, "max_replans": None})
        self.assertIn("Replans are unbounded", text)
        self.assertNotIn("PAUSED_MILESTONE_STALLED", text)

    def test_spent_replans_state_that_any_task_on_the_milestone_pauses(self):
        # The second stall after replan 1 of 1: before_assignment refuses every task on M1.
        spent = {**STALLED, "replans": 1, "reviews_without_progress": 3}
        text = replan.constraint(spent, LIMITS)
        self.assertIn("MILESTONE REPLANS SPENT", text)
        self.assertIn("3 validations without progress (limit 3) and its replans are spent (1 made, limit 1)", text)
        self.assertIn("a next task on M1 with any status, REWORK or CONTINUE,\nincluding a kind=validate", text)
        self.assertIn("PAUSED_MILESTONE_STALLED", text)
        self.assertNotIn("MILESTONE REPLAN REQUIRED", text)
        self.assertEqual(replan.SPENT_VALIDATE_RULE, replan.validate_rule(spent, LIMITS))
        self.assertEqual(replan.REPLAN_VALIDATE_RULE, replan.validate_rule(STALLED, LIMITS))

    def test_spent_replans_name_every_decision_that_still_calls_the_resolver(self):
        # A REWORK, and a BLOCKED of most user_request kinds, call the Resolver before the run pauses;
        # PendingReplanThroughTheCLI checks each kind against ASKS_USER_DIRECTLY.
        text = replan.constraint({**STALLED, "replans": 1}, LIMITS)
        self.assertIn(
            "the Resolver is called first for a REWORK on M1 (it cannot get M1 another task)\n"
            "and for a BLOCKED whose user_request.kind is not permission or goal_change",
            text,
        )
        self.assertIn("a CONTINUE on M1 pauses without the Resolver", text)
        self.assertIn("including which user_request.kind to choose", text)

    def test_a_batch_names_the_members_the_gate_refuses_not_its_row_id(self):
        # before_assignment treats a task as on the batch when next_task.milestone_id is a member.
        self.assertEqual(["M1", "M2"], replan.members(BATCH))
        self.assertEqual(["M1"], replan.members(STALLED))
        for row, gate in ((BATCH, "only with status REWORK"), ({**BATCH, "replans": 1}, "with any status")):
            with self.subTest(gate=gate):
                text = replan.constraint(row, LIMITS)
                self.assertIn("A next task is on this batch when next_task.milestone_id is M1 or M2", text)
                self.assertIn(f"a next task on M1 or M2 {gate}", text)
                self.assertNotIn("on batch:", text)
                self.assertIn("Advancing to a milestone outside the batch still needs", text)
        self.assertIn("if the batch stalls again after it", replan.constraint(BATCH, LIMITS))
        self.assertEqual(["M1", "M2", "M3"], replan.members({**BATCH, "milestone_ids": ["M1", "M2", "M3"]}))
        self.assertIn(
            "on M1, M2 or M3 with any status",
            replan.constraint({**BATCH, "replans": 1, "milestone_ids": ["M1", "M2", "M3"]}, LIMITS),
        )

    def test_nothing_to_state_without_a_pending_replan(self):
        for row, limits in (
            ({**STALLED, "needs_replan": False}, LIMITS),
            (STALLED, {**LIMITS, "stalled_reviews": None}),
        ):
            self.assertEqual("", replan.constraint(row, limits))
            self.assertIsNone(replan.validate_rule(row, limits))

    def test_the_overridden_rule_is_the_one_the_completion_owner_reads(self):
        # If ASTRA_DECISIONS is reworded, the stage context would silently keep the conflicting rule.
        self.assertEqual(1, support.ASTRA_DECISIONS.count(replan.GENERAL_VALIDATE_RULE))
        rewritten = support.ASTRA_DECISIONS.replace(replan.GENERAL_VALIDATE_RULE, replan.REPLAN_VALIDATE_RULE)
        self.assertNotIn("with CONTINUE when existing work only needs Validator revalidation", rewritten)
        self.assertIn("that task is a REWORK, never a CONTINUE", rewritten)
        rewritten = support.ASTRA_DECISIONS.replace(replan.GENERAL_VALIDATE_RULE, replan.SPENT_VALIDATE_RULE)
        self.assertIn("except on any milestone\nnamed in MILESTONE REPLANS SPENT below", rewritten)


class BuilderReassessmentGateTests(unittest.TestCase):
    def fixture(self, *, enabled=True, cap=1):
        task = {
            "id": "T1",
            "kind": "implement",
            "contract_hash": "approved",
            "milestone_id": "M1",
            "objective": "Deliver the greeting",
            "affected_paths": ["greet.py"],
            "requirements": ["Print the approved greeting"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python greet.py"],
        }
        state = {
            "current_task": task,
            "goal_contract": {
                "hash": "approved",
                "body": {"milestones": [{"id": "M1", "objective": task["objective"], "acceptance_criteria": ["C1"]}]},
            },
            "settings": {"milestone_checkpoints": {**LIMITS, "enabled": enabled, "max_replans": cap}},
            "status": "RUNNING",
        }
        evidence = {
            "failure_id": "failure-1",
            "evidence_refs": ["builder.json"],
            "record": {"output": "builder.json", "source_revision": "source"},
        }
        decision = {
            "status": "REWORK",
            "evidence": ["Diagnosed failed approach"],
            "next_objective": task["objective"],
            "affected_paths": task["affected_paths"],
            "next_task": {
                key: task[key]
                for key in ("kind", "milestone_id", "requirements", "acceptance_criteria", "validation_plan")
            },
        }
        return state, evidence, decision

    def test_unchanged_rework_before_any_validation_is_refused_against_current_failed_task(self):
        state, evidence, decision = self.fixture()
        milestones.request_builder_reassessment(state, evidence)
        with self.assertRaises(util.Paused) as caught:
            milestones.before_assignment(state, decision, {"revision": "source"})
        self.assertEqual("PAUSED_MILESTONE_REPLAN", caught.exception.status)
        decision["next_objective"] = "Use the diagnosed smaller implementation approach"
        milestones.before_assignment(state, decision, {"revision": "source"})
        self.assertEqual(1, milestones.progress(state)["replans"])

    def test_failed_current_task_not_an_older_validation_approach_is_the_gate_baseline(self):
        state, evidence, decision = self.fixture()
        milestones.progress(state)["last_approach"] = "older-validation-approach"
        milestones.request_builder_reassessment(state, evidence)
        with self.assertRaises(util.Paused):
            milestones.before_assignment(state, decision, {"revision": "source"})

    def test_disabled_gate_retains_plan_pause_without_enabling_or_creating_marker(self):
        state, evidence, _ = self.fixture(enabled=False)
        configured = copy.deepcopy(state["settings"])
        milestones.request_builder_reassessment(state, evidence)
        self.assertEqual("PAUSED_MILESTONE_REPLAN", state["status"])
        self.assertEqual(configured, state["settings"])
        self.assertNotIn("milestone_progress", state)

    def test_disabling_an_existing_trigger_cannot_bypass_the_assignment_gate(self):
        state, evidence, decision = self.fixture()
        milestones.request_builder_reassessment(state, evidence)
        state["settings"]["milestone_checkpoints"]["enabled"] = False
        for stage in ("terra", "astra_review", "astra_resolve"):
            with self.assertRaises(util.Paused):
                milestones.dispatch_guard(state, stage)
        with self.assertRaises(util.Paused):
            milestones.before_assignment(state, decision, {"revision": "source"})

    def test_disabled_preplanning_without_a_current_assignment_preserves_immediate_return(self):
        for values in (
            {},
            {"current_task": None, "goal_contract": None},
            {"current_task": None, "milestone_progress": {"prior": {"id": "M1"}}},
        ):
            state = {"settings": {"milestone_checkpoints": {"enabled": False}}, **values}
            original = copy.deepcopy(state)
            milestones.before_assignment(state, {}, {"revision": "source"})
            milestones.dispatch_guard(state, "astra_plan")
            self.assertEqual(original, state)


if __name__ == "__main__":
    unittest.main()
