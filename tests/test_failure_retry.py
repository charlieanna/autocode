"""Explicit retries preserve evidence and never replenish failure budgets."""
import copy
import unittest
from unittest.mock import patch

import autocode as runner, autocode_support as support
from . import test_autocode, test_subprocess


class FailureRetryTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp
    tearDown = test_autocode.RetrofitTest.tearDown

    def seed(self):
        self.state.update(status='PAUSED_REPEATED_FAILURE', next_stage='terra')
        revision = support.snapshot(self.root)['revision']
        for stage, attempts in (('sol', 1), ('terra', 3)):
            for index in range(attempts):
                record = {'stage': stage, 'iteration': 1, 'role': stage,
                          'output': str(self.run / f'{stage}-{index}.json'),
                          'source_revision': revision, 'rejected': True}
                runner.failures.record(self.state, record, ValueError('invalid report'), support.now())
                self.state['stages'].append(record)
        support.atomic_json(self.run / 'state.json', self.state)
        return record

    def test_authorization_preserves_all_provenance_and_is_consumed_once(self):
        record = self.seed()
        history = copy.deepcopy(self.state['failure_history'])
        stages = copy.deepcopy(self.state['stages'])
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(history, self.state['failure_history'])
        self.assertEqual(stages, self.state['stages'])
        reloaded = support.read(self.run / 'state.json')
        self.assertEqual(history, reloaded['failure_history'])
        self.assertEqual(stages, reloaded['stages'])
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(reloaded, self.root)
        runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        retry = dict(record, output=str(self.run / 'terra-3.json'))
        retry.pop('failure_attempt', None)
        retry.pop('failure_key', None)
        entry = runner.failures.record(self.state, retry, ValueError('invalid report'), support.now())
        self.state['stages'].append(retry)
        self.assertEqual(4, entry['count'])
        self.assertEqual(4, len(entry['attempts']))
        self.assertTrue(set(history[record['failure_key']]['attempts']) <= set(entry['attempts']))
        # Even a copy of the old unconsumed token cannot authorize count four.
        old = copy.deepcopy(reloaded['failure_retry_authorizations'][-1])
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=old)

    def test_unsafe_or_mismatched_authorization_is_read_only(self):
        self.seed()
        initial = copy.deepcopy(self.state)
        before = (self.run / 'state.json').read_bytes()
        for override in ({'active_stage': {'pid': 123}}, {'active_stage': {'exit_code': 1}},
                         {'uncertain_artifacts': 'unresolved'}, {'pending_report_repair': {'attempts': 2}},
                         {'next_stage': 'sol'}, {'status': 'RUNNING'}, {'failure_history': {}}):
            with self.subTest(override=override):
                state = {**copy.deepcopy(initial), **override}
                snapshot = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    runner.authorize_failure_retry(state, self.run, self.root)
                self.assertEqual(snapshot, state)
                self.assertEqual(before, (self.run / 'state.json').read_bytes())
        with patch.object(runner.support, 'snapshot', return_value={'revision': 'changed'}):
            with self.assertRaises(ValueError):
                runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(initial, self.state)
        self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_fallback_failure_identity_cannot_authorize_a_different_key(self):
        record = self.seed()
        state = copy.deepcopy(self.state)
        # Keep a real repeated fallback but corrupt the last record's key.
        state['stages'][-1]['failure_key'] = 'missing'
        self.assertIsNotNone(runner.failures.repeated(state, state['stages'][-1]))
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            runner.authorize_failure_retry(state, self.run, self.root)
        self.assertEqual(before, state)
        self.assertIn(record['failure_key'], state['failure_history'])


class FailureRetryCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def test_cli_grants_one_attempt_not_a_fresh_failure_budget(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.launch(['Build greeting', '--chat'], 0, answers='CLI\nyes\n')
        run, state = self.saved()
        original = next(row for row in state['stages'] if row['stage'] == 'sol')
        state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', next_stage='sol')
        state['settings']['report_repair'] = {'max_attempts': 0}
        for index in range(3):
            record = dict(original, output=str(run / f'failed-sol-{index}.json'), rejected=True,
                          source_revision=support.snapshot(self.project)['revision'])
            runner.failures.record(state, record, ValueError('Missing verdict'), support.now())
            state['stages'].append(record)
        support.atomic_json(run / 'state.json', state)
        history = copy.deepcopy(state['failure_history'])
        count = len(state['stages'])
        args = ['--run-dir', str(run), '--no-chat', '--resume-paused']
        self.launch(args, 2)
        self.assertEqual(count, len([r for r in self.saved()[1]['stages'] if not r.get('runner_owned')]))
        self.assertEqual(history, self.saved()[1]['failure_history'])
        # Corrupt only the fixture's validation report, never the artifact: a
        # writing Builder changes the source and therefore the failure identity.
        provider = self.root / 'fixture-bin' / 'codex'
        provider.write_text(provider.read_text() + '\nif stage == "sol":\n'
                            '    result.pop("verdict", None)\n'
                            '    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))\n')
        self.launch([*args, '--retry-failed-stage'], 2)
        _, retried = self.saved()
        import autocode_resolver_human as human
        public = human.current(retried)
        self.assertEqual('WAITING_FOR_USER', retried['status'])
        self.assertEqual('PAUSED_REPEATED_FAILURE', retried['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']['pause_status'])
        self.assertEqual(4, retried['failure_history'][record['failure_key']]['count'])
        self.assertEqual(['sol'], [row['stage'] for row in retried['stages'][count:] if not row.get('runner_owned')])
        count = len(retried['stages'])
        self.launch(args, 2)
        self.assertEqual(count, len(self.saved()[1]['stages']))
        self.assertEqual(retried['failure_history'], self.saved()[1]['failure_history'])
