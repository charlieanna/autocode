"""Prompt compression must preserve the information needed to reject bad work."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_context as context
import autocode_stage_context as stage_context


def packet(stage='astra_review'):
    criteria = [{'id': f'C{i}', 'criterion': f'Preserve requirement {i}: ' + 'exact behavior ' * 20,
                 'verification_method': f'Run independent check {i}', 'human_review': i == 18}
                for i in range(1, 19)]
    command = "python3 -c '" + 'assert 1 == 1; ' * 100 + "'"
    return {'stage': stage, 'acceptance_criteria': [
                {k: c[k] for k in ('id', 'criterion')} for c in criteria],
            'goal_contract': {'hash': 'approved-hash', 'revision': 3, 'body': {
                'acceptance_criteria': criteria, 'constraints': ['Never modify authentication']}},
            'validation': {'verdict': 'FAIL', 'checks': [{'command': command, 'exit_code': 0}],
                'check_replay': {'verdict': 'FAIL', 'source_revision': 'current-source', 'checks': [
                    {'command': command, 'exit_code': 1, 'reported_exit_code': 0,
                     'timed_out': False, 'output': 'runner.log', 'output_sha256': 'proof',
                     'error': 'independent failure', 'tail': 'AssertionError'},
                    {'command': 'unreported check', 'exit_code': 1}]}},
            'saved_answers': {'old-denial': {'answer': 'Do not deploy'}},
            'brief_feedback': [{'text': 'Authentication must remain unchanged'}],
            'human_reviews': {'C18': {'approved': False}},
            'unresolved_findings': [{'id': 'F1', 'severity': 'blocking'}]}


def resolve(document, pointer):
    value = document
    for part in pointer.removeprefix('#/').split('/'):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class ReviewHandoffTests(unittest.TestCase):
    def test_scripted_reviewers_return_full_criteria_from_compact_prompts(self):
        original = packet()
        original['current_task'] = {'id': 'task', 'milestone_id': 'M1'}
        small, _ = context.compact(original, '/nonexistent/state.json')
        self.assertIn('acceptance_criteria_ref', small)
        tools = Path(__file__).resolve().parents[1] / 'tools'
        with tempfile.TemporaryDirectory() as tmp:
            for name, output_flag in (('fake_codex.py', '-o'), ('fake_command_tool.py', '--report'),
                                      ('fake_parallel_builder.py', '-o')):
                for data in (original, small):
                    with self.subTest(provider=name, compact=data is small):
                        output = Path(tmp) / 'report.json'
                        result = subprocess.run([sys.executable, str(tools / name), output_flag, str(output)],
                            input='CURRENT HANDOFF DATA\n' + json.dumps(data), text=True,
                            capture_output=True, cwd=tmp, timeout=20)
                        self.assertEqual(0, result.returncode, result.stderr)
                        report = json.loads(output.read_text())
                        self.assertEqual(original['acceptance_criteria'], [
                            {k: c[k] for k in ('id', 'criterion')} for c in report['acceptance_criteria']])

    def test_duplicates_shrink_without_losing_rejection_evidence(self):
        for stage in ('sol', 'astra_review', 'astra_checkpoint'):
            with self.subTest(stage=stage):
                original = packet(stage)
                before = copy.deepcopy(original)
                small, moved = context.compact(original, '/nonexistent/state.json')
                self.assertEqual(before, original)
                self.assertEqual([], moved)
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)) * .8)
                ref = small['acceptance_criteria_ref']
                self.assertEqual(original['acceptance_criteria'], [
                    {k: c[k] for k in ref['fields']} for c in resolve(small, ref['path'])])
                restored = copy.deepcopy(small['validation'])
                for row in restored['check_replay']['checks']:
                    if 'command_ref' in row:
                        row['command'] = resolve(small, row.pop('command_ref'))
                self.assertEqual(original['validation'], restored)
                for key in ('goal_contract', 'saved_answers', 'brief_feedback', 'human_reviews',
                            'unresolved_findings'):
                    self.assertEqual(original[key], small[key])
                self.assertEqual(small, context.compact(small, '/nonexistent/state.json')[0])

    def test_different_criteria_or_commands_are_never_conflated(self):
        original = packet()
        original['acceptance_criteria'][0]['criterion'] = 'Different approved wording'
        original['validation']['check_replay']['checks'][0]['command'] += ' --different'
        self.assertEqual((original, []), context.compact(original, '/nonexistent/state.json'))

    def test_planning_builder_and_legacy_packets_keep_their_shape(self):
        for stage in ('terra', 'astra_discovery', 'astra_plan'):
            original = packet(stage)
            self.assertEqual((original, []), context.compact(original, '/nonexistent/state.json'))
        original = {'stage': 'sol', 'acceptance_criteria': [{'id': 'C1', 'criterion': 'Legacy'}]}
        self.assertEqual((original, []), context.compact(original, '/nonexistent/state.json'))

    def test_small_packets_are_not_made_larger(self):
        original = {'stage': 'sol', 'acceptance_criteria': [{'id': 'C1', 'criterion': 'Works'}],
                    'goal_contract': {'body': {'acceptance_criteria': [{'id': 'C1', 'criterion': 'Works'}]}}}
        self.assertEqual((original, []), context.compact(original, '/nonexistent/state.json'))

    def test_actual_stage_prompt_keeps_the_complete_contract_and_counts_reduced_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = packet()
            state.update(version=3, task='Implement all 18 requirements', workspace=tmp,
                         settings={'roles': {r: {'engine': 'codex', 'model': 'fake'}
                                             for r in ('astra', 'terra', 'sol')}},
                         answers=state['saved_answers'])
            before = copy.deepcopy(state)
            with patch.object(stage_context.support, 'snapshot', return_value={'revision': 'r', 'head': 'h'}):
                for stage in ('sol', 'astra_review', 'astra_checkpoint'):
                    prompt, metrics = stage_context.context_packet(state, stage, Path(tmp) / 'state.json')
                    data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
                    self.assertIn('acceptance_criteria_ref', data)
                    self.assertEqual(state['goal_contract'], data['goal_contract'])
                    self.assertEqual(state['saved_answers'], data['saved_answers'])
                    self.assertEqual((len(prompt.encode()) + 3) // 4, metrics['estimated_prompt_tokens'])
            self.assertEqual(before, state)
