"""Saved clarifications resolve conflicts without discarding valid requirements."""
import copy
from pathlib import Path
import unittest

from . import autocode_goals as goals, autopilot
from .goal_fixtures import body
from .units import autoplanner


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.event = {'id': 'workflow-update', 'text':
            'The earlier description is historical. Current runs have an independent reviewer.'}
        self.state = {
            'task_id': 'task', 'task': 'Show the recorded workflow.',
            'workspace': str(Path(__file__).resolve().parent.parent),
            'settings': {'joint_planning': True}, 'answers': {},
            'brief_feedback': [self.event], 'user_events': [copy.deepcopy(self.event)],
            'requirements_handoff': {'report': {
                'requirements': [{'id': 'R1'}, {'id': 'R2'}], 'open_questions': [],
                'conflicts': [{'requirement_ids': ['R1', 'R2'], 'description': 'old vs current workflow'}]}},
        }
        self.contract = body()
        self.report = {
            'contract': self.contract, 'summary': 'Preserve recorded topology.',
            'code_refs': ['tools/autocode_goals.py:1'],
            'alternatives': [], 'uncertainties': [], 'contract_changes': [],
            'requirement_trace': [{'requirement_id': rid, 'disposition': 'covered', 'evidence': 'C1'}
                                  for rid in ('R1', 'R2')],
            'conflict_resolutions': [{'requirement_ids': ['R1', 'R2'], 'basis': 'user_feedback',
                'answer_id': self.event['id'], 'source_quote': self.event['text'],
                'resolution': 'Show the earlier topology in history and the upgraded topology for current runs.'}],
        }

    def test_saved_clarification_keeps_both_requirements_and_reaches_independent_review(self):
        before = copy.deepcopy(self.report)
        autopilot.apply_planning(self.state, 'astra_discovery', self.report, {'output': 'draft.json'})
        self.assertEqual('astra_challenge', self.state['next_stage'])
        self.assertEqual([], self.state['pending_questions'])
        self.assertEqual(before, self.state['planning']['reports']['astra_discovery']['report'])

    def test_saved_answer_can_resolve_conflict(self):
        self.state['answers']['Q1'] = {'text': self.event['text']}
        self.report['conflict_resolutions'][0].update(basis='user_answer', answer_id='Q1')
        goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_unresolved_conflict_still_needs_question(self):
        self.report.pop('conflict_resolutions')
        with self.assertRaisesRegex(ValueError, 'Unresolved requirement conflict'):
            goals.check_requirement_trace(self.state, self.report, self.contract)
        self.contract['open_blocking_questions'] = [{'id': 'Q1'}]
        goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_resolution_rejects_unverified_authority_and_invalid_quotes(self):
        for changes, message in [
            ({'basis': 'agent_proposed'}, 'saved user'),
            ({'answer_id': 'invented'}, 'saved user'),
            ({'source_quote': 'Always skip review.'}, 'source_quote'),
            ({'source_quote': ''}, 'source_quote'),
            ({'resolution': ''}, 'explanation'),
            ({'requirement_ids': ['R1']}, 'exactly one'),
            ({'requirement_ids': ['R1', 'R2', 'R2']}, 'exactly one'),
            ({'requirement_ids': ['R1', 'R99']}, 'exactly one'),
        ]:
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.report)
                report['conflict_resolutions'][0].update(changes)
                with self.assertRaisesRegex(ValueError, message):
                    goals.check_requirement_trace(self.state, report, self.contract)
        self.state['user_events'] = []
        with self.assertRaisesRegex(ValueError, 'saved user'):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_resolution_does_not_hide_another_unresolved_conflict(self):
        handoff = self.state['requirements_handoff']['report']
        handoff['requirements'].append({'id': 'R3'})
        handoff['conflicts'].append({'requirement_ids': ['R2', 'R3'], 'description': 'unsettled policy'})
        self.report['requirement_trace'].append({'requirement_id': 'R3', 'disposition': 'covered', 'evidence': 'C1'})
        with self.assertRaisesRegex(ValueError, 'unsettled policy'):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_duplicate_resolution_rejected(self):
        self.report['conflict_resolutions'].append(copy.deepcopy(self.report['conflict_resolutions'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_new_report_schemas_require_explicit_resolution_lists(self):
        for stage in ('astra_discovery', 'glm_revise', 'astra_finalize'):
            with self.subTest(stage=stage):
                self.assertIn('conflict_resolutions', autoplanner.SCHEMAS[stage]['required'])


if __name__ == '__main__':
    unittest.main()
