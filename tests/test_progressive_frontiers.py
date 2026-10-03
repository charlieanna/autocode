"""Readonly frontier rules distinguish reviewed future work from the active queue."""
import copy
import unittest

import autocode_progressive_state as progressive
import autocode_stuck_job as stuck


class ProgressiveFrontiersTests(unittest.TestCase):
    def fixture(self, future):
        return {"goal_contract": {"body": {"acceptance_criteria": [{"id": "C1"}]}},
                "progressive": {"version": 1, "plan": {"proposal": {"slices": [
                    {"id": "active", "criterion_ids": ["C1"]}, *future]}},
                    "future": [{"id": "active", "criterion_ids": ["C1"]}]}}

    def test_queued_active_head_does_not_defer_its_complete_product_proof(self):
        state = self.fixture([])
        before = copy.deepcopy(state)
        self.assertEqual(set(), progressive.pending_product_criteria(state))
        self.assertEqual([], progressive.deferred_criteria(
            state, [{"relation": "fully_verify", "criterion_ids": ["C1"]}]))
        self.assertEqual(before, state)

    def test_retained_old_full_target_does_not_close_new_future_capability(self):
        state = self.fixture([{"id": "new-capability", "criterion_ids": ["C1"]}])
        self.assertEqual({"C1"}, progressive.pending_product_criteria(state))
        self.assertEqual(["C1"], progressive.deferred_criteria(
            state, [{"relation": "fully_verify", "criterion_ids": ["C1"]}]))

    def test_lineage_review_exhaustion_is_capacity_not_an_investigation_reset(self):
        state = {"status": "RUNNING", "next_stage": "astra_finalize",
                 "progressive": {"version": 1, "budget": {"pools": {"existing": {"reviews_used": 2}}}}}
        before = copy.deepcopy(state)
        self.assertTrue(progressive.retained_review_budget_pause(state, "PAUSED_PLANNING_BUDGET"))
        self.assertFalse(stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "reviews"))
        self.assertEqual(before, state)
        self.assertFalse(progressive.retained_review_budget_pause(state, "PAUSED_NO_PROGRESS"))
        self.assertFalse(progressive.retained_review_budget_pause({}, "PAUSED_PLANNING_BUDGET"))
