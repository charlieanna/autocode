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

    def test_nothing_to_state_unless_a_replan_is_required(self):
        self.assertEqual("", replan.constraint({**STALLED, "needs_replan": False}, LIMITS))
        self.assertEqual("", replan.constraint({**STALLED, "replans": 1}, LIMITS))

    def test_the_overridden_rule_is_the_one_the_completion_owner_reads(self):
        # If ASTRA_DECISIONS is reworded, the stage context would silently keep the conflicting rule.
        self.assertEqual(1, support.ASTRA_DECISIONS.count(replan.GENERAL_VALIDATE_RULE))
        rewritten = support.ASTRA_DECISIONS.replace(replan.GENERAL_VALIDATE_RULE, replan.REPLAN_VALIDATE_RULE)
        self.assertNotIn("with CONTINUE when existing work only needs Validator revalidation", rewritten)
        self.assertIn("that task is a REWORK, never a CONTINUE", rewritten)


if __name__ == "__main__":
    unittest.main()
