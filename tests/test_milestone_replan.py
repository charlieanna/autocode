"""The pending milestone replan and the Completion Owner's statement of it (issue #459). Pure logic."""
import unittest

import autocode_milestone_replan as replan
import autocode_support as support

LIMITS = {"enabled": True, "max_seconds": 5400, "stalled_reviews": 3, "max_replans": 1}
# The live run's stalled milestone (feature-stock-refusals, run 8soi9a5s).
STALLED = {"id": "M1", "needs_replan": True, "replans": 0, "reviews_without_progress": 4, "rejected_advances": 2}


class PendingReplanTests(unittest.TestCase):
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

    def test_nothing_to_state_without_a_pending_replan(self):
        for row, limits in (({**STALLED, "needs_replan": False}, LIMITS), (STALLED, {**LIMITS, "stalled_reviews": None})):
            self.assertEqual("", replan.constraint(row, limits))
            self.assertIsNone(replan.validate_rule(row, limits))

    def test_the_overridden_rule_is_the_one_the_completion_owner_reads(self):
        # If ASTRA_DECISIONS is reworded, the stage context would silently keep the conflicting rule.
        self.assertEqual(1, support.ASTRA_DECISIONS.count(replan.GENERAL_VALIDATE_RULE))
        rewritten = support.ASTRA_DECISIONS.replace(replan.GENERAL_VALIDATE_RULE, replan.REPLAN_VALIDATE_RULE)
        self.assertNotIn("with CONTINUE when existing work only needs Validator revalidation", rewritten)
        self.assertIn("that task is a REWORK, never a CONTINUE", rewritten)
        rewritten = support.ASTRA_DECISIONS.replace(replan.GENERAL_VALIDATE_RULE, replan.SPENT_VALIDATE_RULE)
        self.assertIn("except on the milestone\nnamed in MILESTONE REPLANS SPENT below", rewritten)


if __name__ == "__main__":
    unittest.main()
