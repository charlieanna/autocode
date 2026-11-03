"""Brief corrections at an idle, unapproved planning handoff."""

import copy
import unittest

import autocode_planning_feedback as feedback


class PlanningBoundaryTests(unittest.TestCase):
    def test_feedback_admission_includes_an_idle_draft_handoff(self):
        self.assertTrue(
            feedback.admitted(
                {
                    "status": "RUNNING",
                    "phase": "PLANNING",
                    "next_stage": "astra_challenge",
                    "goal_contract": {"approval_status": "draft"},
                }
            )
        )

    def test_idle_unapproved_planning_handoff_accepts_feedback(self):
        state = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "next_stage": "astra_challenge",
            "goal_contract": {"approval_status": "draft", "approval_event": None},
        }
        self.assertTrue(feedback.between_stages(state))
        self.assertTrue(feedback.admitted(state))
        for change in (
            {"status": "PAUSED_TIME_LIMIT"},
            {"phase": "EXECUTING"},
            {"next_stage": "terra"},
            {"next_stage": None},
            {"goal_contract": None},
            {"goal_contract": {"approval_status": "approved"}},
            {"goal_contract": {"approval_event": {"token": "approved"}}},
            {"active_stage": {"stage": "astra_challenge"}},
            {"active_runner_check": {"id": "check"}},
            {"uncertain_artifacts": ["saved-report"]},
            {"pending_report_repair": {"stage": "astra_challenge"}},
            {"pending_questions": [{"id": "Q1"}]},
            {"user_request": {"kind": "human_review"}},
        ):
            with self.subTest(change=change):
                self.assertFalse(feedback.between_stages({**copy.deepcopy(state), **change}))
