"""Recovery narrows a current reviewer call without granting or rerouting work."""

import copy
import unittest

import autocode_planning_recovery_policy as policy


class PlanningRecoveryPolicyTests(unittest.TestCase):
    def test_ordinary_and_other_stage_work_is_not_report_only(self):
        for state in (
            {},
            {"recovery_context": {"stage": "astra_finalize"}},
            {"recovery_context": {"stage": "astra_challenge", "next_stage": "glm_revise"}},
        ):
            self.assertFalse(policy.active(state, "astra_challenge"))
        self.assertFalse(policy.active({"recovery_context": {"stage": "terra"}}, "terra"))

    def test_existing_recovery_and_unused_grant_narrow_without_mutating_authority(self):
        states = [
            {"recovery_context": {"stage": "astra_challenge", "next_stage": "astra_challenge"}},
            {"planning": {"recovery_review_grants": [{"binding": {"stage": "astra_challenge"}, "consumed": False}]}},
        ]
        for state in states:
            before = copy.deepcopy(state)
            self.assertTrue(policy.active(state, "astra_challenge"))
            self.assertEqual(before, state)
        states[1]["planning"]["recovery_review_grants"][0]["consumed"] = True
        self.assertFalse(policy.active(states[1], "astra_challenge"))
