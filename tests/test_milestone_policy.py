"""Offline regression checks for milestone handoffs, not model behavior claims."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goals as goals
import autocode_milestones as milestones
import autocode_planning as planning
import autocode_stage_context as stage_context
import autocode_support as support
from units import autoreview


class MilestonePolicyTests(unittest.TestCase):
    def state(self):
        return {
            "version": 3,
            "task": "Preserve existing course",
            "workspace": "/fixture",
            "acceptance_criteria": [],
            "settings": {
                "engine": "opencode",
                "joint_planning": True,
                "limits": {"iteration_ceiling": 23},
                "roles": {
                    "terra": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "max"}
                },
            },
        }

    def test_every_execution_role_gets_policy_without_state_mutation(self):
        for stage in ("astra_plan", "terra", "sol", "astra_review", "astra_discovery"):
            with self.subTest(stage=stage):
                state = self.state()
                before = copy.deepcopy(state)
                with patch.object(support, "snapshot", return_value={"revision": "r1", "head": "h1"}):
                    prompt, _ = stage_context.context_packet(state, stage, Path("/fixture/state.json"))
                self.assertEqual(1, prompt.count("MILESTONE HANDOFF POLICY v1"))
                self.assertIn("never a minimum duration", prompt)
                self.assertIn("independently audits", prompt)
                self.assertEqual(before, state)

    def test_final_validation_is_conditional_in_initial_and_send_back_handoffs(self):
        for stage in ("astra_review", "astra_checkpoint"):
            for evidence_ready in (False, True):
                for sent_back in (False, True):
                    with self.subTest(stage=stage, evidence_ready=evidence_ready, sent_back=sent_back):
                        state = self.state()
                        if sent_back:
                            state.update(autoreview.completion_review(state, {"revision": "r1"}))
                        before = copy.deepcopy(state)
                        checkpoint = {
                            "current": {"id": "M1", "status": "PENDING"},
                            "limits": {"max_replans": 1, "stalled_reviews": 2},
                        }
                        with (
                            patch.object(support, "snapshot", return_value={"revision": "r1", "head": "h1"}),
                            patch.object(milestones, "enabled", return_value=True),
                            patch.object(milestones, "summary", return_value=checkpoint),
                            patch.object(milestones, "scope", return_value={"members": []}),
                            patch.object(milestones, "evidence_ready", return_value=evidence_ready),
                        ):
                            prompt, _ = stage_context.context_packet(state, stage, Path("/fixture/state.json"))
                        instruction, payload = prompt.split("CURRENT HANDOFF DATA\n", 1)
                        instruction = " ".join(instruction.split())
                        self.assertIn(
                            "Assign a final kind=validate task on the current milestone only when current, "
                            "independent passing evidence is still needed for a contract criterion or the complete "
                            "approved flow on this artifact.",
                            instruction,
                        )
                        self.assertIn(
                            "When all that evidence already passes, propose COMPLETE subject to the full-task "
                            "completion gate",
                            instruction,
                        )
                        self.assertIn("Never weaken the full-task completion gate.", instruction)
                        self.assertIn(
                            "It must list every contract criterion and may recheck accepted milestones' criteria.",
                            instruction,
                        )
                        self.assertNotIn("For that final validation, assign a kind=validate task", instruction)
                        packet = json.loads(payload)
                        self.assertEqual(evidence_ready, packet["milestone_checkpoint"]["current_evidence_ready"])
                        if sent_back:
                            self.assertIn(
                                "Re-running validation that already passed is not a reason to continue.",
                                packet["checkpoint_reason"],
                            )
                        self.assertEqual(before, state)

    def test_joint_planning_includes_same_sizing_and_safety_policy(self):
        state = self.state()
        for stage in planning.STAGES:
            prompt, _ = planning.context(state, stage, "/fixture/state.json")
            if stage == "requirements_gather":
                self.assertNotIn("MILESTONE HANDOFF POLICY v1", prompt)
                self.assertIn("Do not create a technical approach, milestone, dependency graph", prompt)
            else:
                self.assertEqual(1, prompt.count("MILESTONE HANDOFF POLICY v1"))
                self.assertIn("never bypass limits", prompt)
                self.assertIn("grants no new goal or permission approval", prompt)

    def test_old_microtask_instruction_removed_but_approval_retained(self):
        self.assertNotIn("Make it small and executable", planning.PROMPTS["astra_finalize"])
        self.assertNotIn("and small milestones", goals.DISCOVERY_PROMPT)
        self.assertIn("user must approve this exact plan", planning.PROMPTS["astra_finalize"])
        self.assertIn("Do not start implementation before that approval", goals.DISCOVERY_PROMPT)

    def test_unapproved_goal_still_cannot_execute(self):
        with self.assertRaises(support.Paused):
            goals.execution_guard(self.state())


if __name__ == "__main__":
    unittest.main()
