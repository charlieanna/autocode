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

    # A held external_directory denial (#301). The CLI flow is in test_permission_recovery_cli.
    def denial(self, number, repeat, since, revision):
        attempt = f'001/builder-{number:02d}'
        return {'attempt_id': attempt, 'incident_id': 'incident-1', 'events': str(self.run / f'{attempt}.jsonl'),
                'denied_operation': {'capability': 'external_directory', 'path': '/tmp/diagnostic/*',
                                     'classification': 'external_temporary_directory'},
                'diagnostic_directory': str(self.root / 'scratch'), 'repeat_count': repeat,
                'denied_since_accepted': since, 'source_revision': revision, 'next_stage': 'terra'}

    def hold(self, *recoveries, **changes):
        revision = support.snapshot(self.root)['revision']
        rows = [self.denial(*row, revision) for row in recoveries or ((1, 1, 1), (2, 2, 2))]
        self.state['settings']['limits'] = {'no_progress_batches': 3}
        self.state.update(status='PAUSED_REPEATED_FAILURE', next_stage='terra', no_progress_batches=len(rows),
                          automatic_permission_recoveries=rows, recovery_context=dict(rows[-1]), **changes)
        support.atomic_json(self.run / 'state.json', self.state)
        self.addCleanup(runner.failure_retry.disarm)
        return revision

    def target(self, state, cause=None, revision=None):
        current = revision or support.snapshot(self.root)['revision']
        return runner.failure_retry.target(state, cause=cause or state['status'], revision=lambda: current)

    def launch_attempt(self, number):
        return {'stage': 'terra', 'iteration': 1, 'output': str(self.run / f'builder-{number:02d}.json'),
                'started_at': support.now()}

    def test_a_saved_denial_authorization_lifts_the_hold_only_for_the_command_that_armed_it(self):
        self.hold()
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual('permission_hold', authorization['kind'])
        saved = support.read(self.run / 'state.json')
        for state in (saved, self.state):
            with self.assertRaisesRegex(support.Paused, 'Repeated external_directory denial'):
                runner.timeout_recovery_guard(state)  # recorded, but no command armed it
        runner.failure_retry.arm(authorization)
        runner.timeout_recovery_guard(self.state)
        runner.failure_retry.launched(self.state, self.launch_attempt(3))
        with self.assertRaisesRegex(support.Paused, 'Repeated external_directory denial'):
            runner.timeout_recovery_guard(self.state)  # consumed by the attempt it launched
        self.assertIsNone(self.target(self.state), 'one stopped attempt launches at most one authorized attempt')
        with self.assertRaises(ValueError):
            runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(1, [e['kind'] for e in self.state['user_events']].count('failure_retry_authorized'))

    def test_reissuing_before_the_attempt_launches_reuses_the_authorization(self):
        self.hold()
        first = runner.authorize_failure_retry(self.state, self.run, self.root)
        again = runner.authorize_failure_retry(support.read(self.run / 'state.json'), self.run, self.root)
        self.assertEqual(first, again)
        saved = support.read(self.run / 'state.json')
        self.assertEqual(1, len(saved['failure_retry_authorizations']))
        self.assertEqual(1, [e['kind'] for e in saved['user_events']].count('failure_retry_authorized'))

    def test_the_authorized_attempt_meets_the_recorded_budget_not_the_no_progress_estimate(self):
        # Three Builder denials: spent() estimates 3 recoveries from no_progress_batches, none recorded.
        self.hold((1, 1, 1), (2, 2, 2), (3, 3, 3))
        self.assertEqual(3, runner.recovery_count(self.state))
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        runner.failure_retry.arm(authorization)
        runner.timeout_recovery_guard(self.state)
        # A spent recorded budget stops it, so the stop never advertises it.
        self.state['automatic_recoveries_since_resume'] = runner.MAX_AUTOMATIC_RECOVERIES
        self.assertIsNone(self.target(self.state))
        with self.assertRaisesRegex(support.Paused, 'budget exhausted'):
            runner.timeout_recovery_guard(self.state)

    def test_only_a_denial_hold_at_its_unchanged_frontier_is_retryable(self):
        revision = self.hold()
        self.assertEqual('001/builder-02', self.target(self.state)['attempt_id'])
        ceiling = copy.deepcopy(self.state)
        ceiling['automatic_permission_recoveries'] = [self.denial(4, 1, 3, revision)]
        ceiling['recovery_context'] = dict(ceiling['automatic_permission_recoveries'][0])
        self.assertIsNotNone(self.target(ceiling), 'the denial ceiling holds too')
        cases = {
            'first denial retries automatically': lambda s: s.update(
                automatic_permission_recoveries=[self.denial(1, 1, 1, revision)],
                recovery_context=self.denial(1, 1, 1, revision)),
            'a later recovery replaced the context': lambda s: s.update(recovery_context={
                'attempt_id': '001/builder-03', 'events': 'other.jsonl', 'timeout_kind': 'idle'}),
            'next stage moved': lambda s: s.update(next_stage='sol'),
            'attempt still active': lambda s: s.update(active_stage={'stage': 'terra'}),
            'report repair pending': lambda s: s.update(pending_report_repair={'attempts': 1}),
            'a requirements question is pending': lambda s: s.update(pending_questions=[{'id': 'q1'}]),
        }
        for name, change in cases.items():
            with self.subTest(name):
                state = copy.deepcopy(self.state)
                change(state)
                self.assertIsNone(self.target(state))
        self.assertIsNone(self.target(self.state, revision='edited'), 'the source changed')
        self.assertIsNone(self.target(self.state, cause='PAUSED_RESOLVER_OPERATIONAL'))
        self.assertIsNone(self.target(self.state, cause='PAUSED_TIME_LIMIT'))

        def unreadable():
            raise OSError('workspace gone')
        self.assertIsNone(runner.failure_retry.target(self.state, cause=self.state['status'], revision=unreadable))


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
        # Seed the same failure the corrupted provider below reproduces: a saved report
        # without its verdict. Only consecutive identical failures count as repetition.
        report = support.read(original['output'])
        report.pop('verdict', None)
        for index in range(3):
            output = run / f'failed-sol-{index}.json'
            support.atomic_json(output, report)
            record = dict(original, output=str(output), rejected=True,
                          source_revision=support.snapshot(self.project)['revision'])
            runner.failures.record(state, record, ValueError('$: missing verdict'), support.now())
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
