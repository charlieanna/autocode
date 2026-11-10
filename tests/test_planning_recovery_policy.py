"""Recovery narrows a current reviewer call without granting or rerouting work."""

import copy
import unittest

import autocode_planning_recovery_policy as policy


class PlanningRecoveryPolicyTests(unittest.TestCase):
    def test_ordinary_and_other_stage_work_is_not_report_only(self):
        for state in (
            {},
            {"recovery_context": {"stage": "finalize_plan"}},
            {"recovery_context": {"stage": "review_plan", "next_stage": "revise_plan"}},
        ):
            self.assertFalse(policy.active(state, "review_plan"))
        self.assertFalse(policy.active({"recovery_context": {"stage": "builder"}}, "builder"))

    def test_existing_recovery_and_unused_grant_narrow_without_mutating_authority(self):
        states = [
            {"recovery_context": {"stage": "review_plan", "next_stage": "review_plan"}},
            {"planning": {"recovery_review_grants": [{"binding": {"stage": "review_plan"}, "consumed": False}]}},
        ]
        for state in states:
            before = copy.deepcopy(state)
            self.assertTrue(policy.active(state, "review_plan"))
            self.assertEqual(before, state)
        states[1]["planning"]["recovery_review_grants"][0]["consumed"] = True
        self.assertFalse(policy.active(states[1], "review_plan"))
