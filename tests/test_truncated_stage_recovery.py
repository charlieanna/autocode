"""One bounded fresh-request retry for a provider turn cut off by the output token limit."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import autocode as runner
import autocode_stage_recovery as recovery
import autocode_support as support
from tests import test_subprocess as subprocess_test_support


class TruncatedStageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / '.autocode/runs/fixture'
        self.run.mkdir(parents=True)
        self.snapshot = {'revision': 'source-revision', 'head': 'head', 'files': {}}

    def attempt(self, stage='astra_plan', ordinal=1):
        base = self.run / 'iterations/001' / f'{stage}-{ordinal:02d}'
        base.parent.mkdir(parents=True, exist_ok=True)
        before = Path(str(base) + '.before.json')
        before.write_text(json.dumps(self.snapshot))
        events = Path(str(base) + '.jsonl')
        session, message = 'ses_limit', f'msg_{ordinal}'
        events.write_text('\n'.join(json.dumps(row) for row in [
            {'type': 'step_start', 'sessionID': session, 'part': {'id': 'p_start', 'sessionID': session}},
            {'type': 'text', 'sessionID': session, 'part': {'id': 'p_text', 'sessionID': session,
                'messageID': message, 'text': '{"plan":"truncat'}},
            {'type': 'step_finish', 'sessionID': session, 'part': {'id': 'p_finish', 'sessionID': session,
                'messageID': message, 'reason': 'length', 'tokens': {'input': 12, 'output': 7,
                'reasoning': 15, 'cache': {'read': 2, 'write': 0}}}},
        ]) + '\n')
        return {'role': 'glm', 'route_role': 'glm', 'stage': stage,
            'iteration': 1, 'started_at': '2026-10-03T00:00:00+00:00', 'finished_at': '2026-10-03T00:00:02+00:00',
            'duration_seconds': 2, 'exit_code': 0, 'events': str(events), 'output': str(base) + '.json',
            'before_ref': str(before), 'engine': 'opencode', 'processes': [],
            'supports_sessions': True, 'source_revision': 'source-revision', 'contract_hash': 'contract-hash'}

    def state(self, record):
        return {'status': 'RUNNING', 'workspace': str(self.root), 'next_stage': record['stage'],
            'iteration': 1, 'active_seconds': 0, 'active_stage': record,
            'settings': {'roles': {}}, 'goal_contract': {'hash': 'contract-hash'},
            'stages': [], 'history': [], 'sessions': {'glm': 'ses_limit'}}

    def invoke_recovery(self, state, error=None):
        error = error or support.Paused('PAUSED_UNCERTAIN_STAGE',
                                        'OpenCode exhausted its output token limit (finish reason: length).')
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        with patch.object(support, 'snapshot', return_value=self.snapshot), \
             patch.object(recovery.interventions, 'pending', return_value=[]), \
             patch.object(recovery.planning, 'is_planning', return_value=True), \
             patch.object(recovery.records, 'write_json', side_effect=save):
            return recovery.automatically_recover_truncated_stage(state, self.run, self.root, error)

    def test_recovery_re_dispatches_stage_with_usage_accounted_and_receipt(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        self.assertNotIn('active_stage', state)
        self.assertEqual(('RUNNING', 'PLANNING', 'astra_plan'),
                         (state['status'], state['phase'], state['next_stage']))
        self.assertEqual({}, state['sessions'])
        [recoveries] = [[row['stage'] for row in state['automatic_truncation_recoveries']]]
        self.assertEqual(['astra_plan'], recoveries)
        self.assertEqual(1, state['automatic_recoveries_since_resume'])
        self.assertEqual('automatic_truncation_recovery', state['user_events'][-1]['kind'])
        original = next(row for row in state['stages'] if row['stage'] == 'astra_plan')
        self.assertTrue(original['truncated_output'])
        self.assertTrue(original['accounted'])
        self.assertEqual(22, original['metrics']['provider_tokens']['output_tokens'])
        self.assertTrue(Path(original['events']).is_file())

    def test_second_truncation_of_same_stage_pauses_instead_of_retrying(self):
        state = self.state(self.attempt())
        self.assertTrue(self.invoke_recovery(state))
        state['active_stage'] = self.attempt(ordinal=2)
        with self.assertRaisesRegex(support.Paused, 'already used its one automatic'):
            self.invoke_recovery(state)
        self.assertEqual(['astra_plan'], [row['stage'] for row in state['automatic_truncation_recoveries']])
        self.assertEqual(1, state['automatic_recoveries_since_resume'])

    def test_run_wide_limit_pauses_after_two_stages(self):
        state = self.state(self.attempt())
        state['automatic_truncation_recoveries'] = [
            {'stage': 'glm_revise'}, {'stage': 'sol'}]
        with self.assertRaisesRegex(support.Paused, 'retry limit reached'):
            self.invoke_recovery(state)

    def test_changed_source_completed_turn_and_other_statuses_do_not_recover(self):
        record = self.attempt()
        state = self.state(record)
        changed = {**self.snapshot, 'revision': 'changed'}
        with patch.object(support, 'snapshot', return_value=changed), \
             patch.object(recovery.interventions, 'pending', return_value=[]):
            self.assertFalse(recovery.automatically_recover_truncated_stage(
                state, self.run, self.root,
                support.Paused('PAUSED_UNCERTAIN_STAGE', 'OpenCode exhausted its output token limit')))
        self.assertIn('active_stage', state)
        completed = self.attempt(ordinal=2)
        Path(completed['events']).write_text(json.dumps(
            {'type': 'turn.completed', 'usage': {'input_tokens': 1}}) + '\n')
        state = self.state(completed)
        self.assertFalse(self.invoke_recovery(state))
        state = self.state(self.attempt(ordinal=3))
        self.assertFalse(self.invoke_recovery(
            state, support.Paused('PAUSED_PROVIDER_UNCERTAIN', 'Output token limit')))
        timed_out = self.attempt(ordinal=4)
        timed_out['timed_out'] = True
        state = self.state(timed_out)
        self.assertFalse(self.invoke_recovery(state))


class TruncatedStageCliTests(unittest.TestCase):
    new_run_engine_args = ('--engine', 'opencode')

    def setUp(self):
        subprocess_test_support.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / 'tools' / 'fake_opencode.py'
        provider = self.root / 'fixture-bin' / 'opencode'
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'

    launch = subprocess_test_support.SubprocessFlow.launch
    saved = subprocess_test_support.SubprocessFlow.saved

    def test_truncated_planning_stage_retries_once_then_pauses(self):
        self.env['AUTOCODE_FIXTURE_TRUNCATE_STAGE'] = 'requirements_gather'
        self.launch(['Build a greeting tool', '--chat'], 2, answers='CLI\nyes\n')
        _, state = self.saved()
        self.assertEqual('WAITING_FOR_USER', state['status'])
        self.assertIn('already used its one automatic output-token-limit retry', state['stop_reason'])
        archived = [row for row in state['stages'] if row['stage'] == 'requirements_gather']
        self.assertEqual(1, len(archived))
        self.assertTrue(archived[0]['truncated_output'])
        self.assertTrue(archived[0]['accounted'])
        self.assertEqual('requirements-gather-02.jsonl', Path(state['active_stage']['events']).name)
        self.assertEqual(['requirements_gather'],
                         [row['stage'] for row in state['automatic_truncation_recoveries']])
        self.assertEqual('automatic_truncation_recovery', state['user_events'][-1]['kind'])


if __name__ == '__main__':
    unittest.main()
