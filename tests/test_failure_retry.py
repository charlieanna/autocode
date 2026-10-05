"""Explicit retries preserve evidence and never replenish failure budgets."""
import copy
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import autocode as runner, autocode_support as support
from . import test_autocode, test_subprocess

FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")


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
                         {'pending_report_repair': {'original': {'failure_key': 'another failure'}, 'attempts': 2}},
                         {'job_failure': {'stage': 'terra'}}, {'pending_questions': [{'id': 'q1'}]},
                         {'next_stage': 'sol'}, {'status': 'RUNNING'}, {'failure_history': {}}):
            with self.subTest(override=override):
                state = {**copy.deepcopy(initial), **override}
                snapshot = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    runner.authorize_failure_retry(state, self.run, self.root)
                self.assertEqual(snapshot, state)
                self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_a_changed_source_authorizes_one_attempt_bound_to_the_failures_revision(self):
        # #302: an operator edit is new evidence; the attempt runs on the edited source.
        record = self.seed()
        with patch.object(runner.support, 'snapshot', return_value={'revision': 'edited'}):
            authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
            runner.repeated_failure_resume_guard(self.state, self.root)  # an edited source is not held
        self.assertEqual((record['failure_key'], record['source_revision']),
                         (authorization['failure_key'], authorization['source_revision']))
        self.assertEqual(1, len(self.state['failure_retry_authorizations']))
        self.assertEqual(3, self.state['failure_history'][record['failure_key']]['count'], 'nothing is reset')

    def test_a_stalled_failures_spent_repair_is_archived_and_one_attempt_authorized(self):
        # #254: a stalled incident is never repaired again, so its exhausted repair no longer blocks the flag.
        record = self.seed()
        pending = {'original': copy.deepcopy(record), 'attempts': 2, 'pins': {}, 'error': 'invalid report'}
        self.state['pending_report_repair'] = copy.deepcopy(pending)
        support.atomic_json(self.run / 'state.json', self.state)
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        reloaded = support.read(self.run / 'state.json')
        for state in (self.state, reloaded):
            self.assertNotIn('pending_report_repair', state)
            self.assertEqual({'reason': 'Explicit authorized retry of a stalled failure', 'repair': pending},
                             {key: state['report_repair_archive'][-1][key] for key in ('reason', 'repair')})
            self.assertNotIn('terra', state['sessions'])
            self.assertEqual(('terra', 't-session'), (state['session_rotations'][-1]['role'],
                                                      state['session_rotations'][-1]['old_session']))
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(reloaded, self.root)  # a reload never carries the authorization
        runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)
        with self.assertRaises(support.Paused):
            runner.repeated_failure_resume_guard(self.state, self.root, authorization=authorization)

    def test_a_published_hold_accepts_the_flag_after_its_binding_went_stale(self):
        record = self.seed()
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, self.state, self.run, support.Paused('PAUSED_REPEATED_FAILURE', 'Unchanged terra failed')))
        runner.write_json(self.run / 'state.json', self.state)  # the writer boundary publishes it
        published = self.state['resolver_human_request']
        self.assertIn('--retry-failed-stage', published['request']['decision_needed'])
        # The resume renews a spent resolver evaluation before the flag is read, recording a user event.
        self.state['resolver'].setdefault('attempts', {})['blocker'] = 1
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertIsNone(runner.resolver_human.current(self.state), 'the request binding is now stale')
        authorization = runner.authorize_failure_retry(self.state, self.run, self.root)
        self.assertEqual(record['failure_key'], authorization['failure_key'])
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        self.assertEqual('superseded', self.state['resolver']['human_escalations'][published['request_id']]['status'])

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

    # #254: a Validator report whose check passes only in the Validator's own session. The runner's
    # replay fails it the same way every time; only the receipt directory differs per attempt.
    def stalled_rejection(self):
        self.probe = self.root / 'launches.jsonl'
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK='1',
                        AUTOCODE_REGISTRY_LAUNCH_PROBE=str(self.probe))
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        launched = self.launched()
        return launched[launched.index('sol'):]

    def launched(self):
        return [json.loads(line)['stage'] for line in self.probe.read_text().splitlines()]

    def pause_status(self, state):
        """The pause itself, or the one behind the published request (whose status the run then saves)."""
        return runner.failure_retry.stop_cause(state, state.get('resolver_human_request'))

    def advertised(self, state):
        published = state.get('resolver_human_request') or {}
        request = published.get('request') or {}
        texts = [state.get('stop_reason') or '', request.get('decision_needed') or '', *(request.get('options') or []),
                 *((q.get('question') or '') for q in published.get('questions') or [])]
        return set(FLAGS.findall(' '.join(texts)))

    def failure_group(self, run):
        view = json.loads(self.launch(['--run-dir', str(run), '--status'], 0).stdout)['view']
        group = view['recovery']['failure_groups'][0]
        return {key: group.get(key) for key in ('count', 'streak', 'authorized_retries')}

    def test_a_replayed_rejection_holds_at_its_third_attempt_and_each_retry_buys_one(self):
        self.assertEqual(['sol', 'sol_report_repair', 'sol_report_repair', 'investigate_stuck'],
                         self.stalled_rejection())
        run, state = self.saved()
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.pause_status(state))
        self.assertIn('`test -f .autocode/validator-only` was reported as exit 0', state['stop_reason'])
        history, investigations = state['failure_history'], state['stuck_investigations']
        seen = len(self.launched())
        # Neither a resume, a chat resume nor a restart is new evidence or authority.
        for extra, answers in ((['--resume-paused', '--no-chat'], None), (['--resume-paused', '--no-chat'], None),
                               (['--resume-paused', '--chat'], ''), (['--no-chat'], None)):
            with self.subTest(extra=extra):
                result = self.launch(['--run-dir', str(run), *extra], 2, answers=answers)
                self.assertNotIn('Input rejected', result.stderr)
                self.assertEqual(seen, len(self.launched()), result.stdout + result.stderr)
                _, after = self.saved()
                self.assertEqual(history, after['failure_history'])
                self.assertEqual(investigations, after['stuck_investigations'])
        _, state = self.saved()  # the first invocation after the pause published the request
        self.assertTrue((state.get('resolver_human_request') or {}).get('request_id'))
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.pause_status(state))
        # Only accepted actions are advertised (#288); --resolver-response is exercised by the sound control.
        self.assertEqual({'--resume-paused', '--retry-failed-stage', '--resolver-response'}, self.advertised(state))
        for authorized in (1, 2):
            with self.subTest(authorized=authorized):
                result = self.launch(['--run-dir', str(run), '--no-chat', '--resume-paused', '--retry-failed-stage'], 2)
                self.assertIn('Failure retry authorized for the recorded repeated failure', result.stdout)
                self.assertEqual(['sol'], self.launched()[seen:], 'one fresh attempt: no repair, no Investigator')
                seen = len(self.launched())
                _, after = self.saved()
                self.assertEqual('PAUSED_REPEATED_FAILURE', self.pause_status(after))
                self.assertEqual({'--resume-paused', '--retry-failed-stage', '--resolver-response'},
                                 self.advertised(after))
                self.assertEqual({'count': 3 + authorized, 'streak': 3 + authorized, 'authorized_retries': authorized},
                                 self.failure_group(run))
        self.assertEqual(2, len(after['failure_retry_authorizations']))
        self.assertTrue(all(row['launched_attempt'] for row in after['failure_retry_authorizations']))

    def test_corrective_information_reaches_the_one_authorized_attempt_which_completes(self):
        self.stalled_rejection()
        run, _ = self.saved()
        self.launch(['--run-dir', str(run), '--no-chat', '--resume-paused'], 2)  # publishes the request
        _, state = self.saved()
        published = state['resolver_human_request']
        note = 'Cite only checks that pass from the repository root in a clean checkout.'
        self.launch(['--run-dir', str(run), '--no-chat', '--resolver-request', published['request_id'],
                     '--resolver-token', published['request_token'], '--resolver-response', 'provide_information',
                     '--resolver-message', note], 0)
        seen = len(self.launched())
        held = self.launch(['--run-dir', str(run), '--no-chat', '--resume-paused'], 2)
        self.assertIn('retained the human guidance', held.stdout)
        self.assertIn('--retry-failed-stage', held.stdout, 'the hold names the one action that moves it')
        self.assertEqual(seen, len(self.launched()), 'information is not authority')
        self.launch(['--run-dir', str(run), '--no-chat', '--resume-paused', '--retry-failed-stage'], 0)
        self.assertEqual(['sol', 'astra_review'], self.launched()[seen:])
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        fresh = [row for row in done['stages'] if row['stage'] == 'sol'][-1]
        self.assertIn(note, Path(fresh['prompt']).read_text(), 'the information reaches the authorized attempt')
        replay = json.loads(self.launch(['--run-dir', str(run), '--status'], 0).stdout)['view']['evidence']['check_replay']
        self.assertEqual('PASS', replay['verdict'])
        self.assertEqual(done['validation']['source_revision'], replay['source_revision'])
        receipts = [json.loads(path.read_text())['verdict'] for path in (run / 'check-replay').glob('*/replay.json')]
        self.assertEqual(3, receipts.count('FAIL'), 'the rejected attempts keep their receipts')
        self.assertEqual([3], [entry['count'] for entry in done['failure_history'].values()])
        rows = done['failure_retry_authorizations']
        self.assertEqual(['repeated_failure'], [row['kind'] for row in rows])
        self.assertTrue(rows[0]['launched_attempt'])
