"""Offline regression checks for milestone handoffs, not model behavior claims."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from . import autocode_support as support
from . import autocode_planning as planning
from . import autocode_goals as goals


class MilestonePolicyTests(unittest.TestCase):
    def state(self):
        return {'version': 3, 'task': 'Preserve existing course', 'workspace': '/fixture',
                'acceptance_criteria': [], 'settings': {'engine': 'opencode',
                'joint_planning': True, 'limits': {'iteration_ceiling': 23},
                'roles': {'terra': {'engine': 'opencode', 'model': 'zai-coding-plan/glm-5.3',
                                    'reasoning_effort': 'max'}}}}

    def test_every_execution_role_gets_policy_without_state_mutation(self):
        for stage in ('astra_plan', 'terra', 'sol', 'astra_review', 'astra_discovery'):
            with self.subTest(stage=stage):
                state = self.state()
                before = copy.deepcopy(state)
                with patch.object(support, 'snapshot', return_value={'revision': 'r1', 'head': 'h1'}):
                    prompt, _ = support.context_packet(state, stage, Path('/fixture/state.json'))
                self.assertEqual(1, prompt.count('MILESTONE HANDOFF POLICY v1'))
                self.assertIn('never a minimum duration', prompt)
                self.assertIn('independently audits', prompt)
                self.assertEqual(before, state)

    def test_joint_planning_includes_same_sizing_and_safety_policy(self):
        state = self.state()
        for stage in planning.STAGES:
            prompt, _ = planning.context(state, stage, '/fixture/state.json')
            if stage == 'requirements_gather':
                self.assertNotIn('MILESTONE HANDOFF POLICY v1', prompt)
                self.assertIn('Do not create a technical approach, milestone, dependency graph', prompt)
            else:
                self.assertEqual(1, prompt.count('MILESTONE HANDOFF POLICY v1'))
                self.assertIn('never bypass limits', prompt)
                self.assertIn('grants no new goal or permission approval', prompt)

    def test_old_microtask_instruction_removed_but_approval_retained(self):
        self.assertNotIn('Make it small and executable', planning.PROMPTS['astra_finalize'])
        self.assertNotIn('and small milestones', goals.DISCOVERY_PROMPT)
        self.assertIn('user must approve this exact plan', planning.PROMPTS['astra_finalize'])
        self.assertIn('Do not start implementation before that approval', goals.DISCOVERY_PROMPT)

    def test_unapproved_goal_still_cannot_execute(self):
        with self.assertRaises(support.Paused):
            goals.execution_guard(self.state())


if __name__ == '__main__':
    unittest.main()
