"""An operator can recover a serial retry exhaustion through the public CLI."""
import copy
import json
import subprocess
import sys
import unittest

import autocode_builder_recovery as recovery
from . import test_subprocess as flow


class SerialRecoveryTests(unittest.TestCase):
    new_run_engine_args = flow.SubprocessFlow.new_run_engine_args
    setUp = flow.SubprocessFlow.setUp
    launch = flow.SubprocessFlow.launch
    saved = flow.SubprocessFlow.saved

    def status(self, run):
        return json.loads(self.launch(['--run-dir', str(run), '--status'], 0).stdout)

    def stopped_run(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'stalled'
        self.launch(['Build greeting', '--chat', '--pin-model-role', 'terra'], 2, answers='CLI\nno\n')
        run, _ = self.saved()
        # Isolate the Builder allowance from the separate stalled-review policy.
        self.launch(['--run-dir', str(run), '--approve-goal', self.status(run)['contract_token'],
                     '--max-milestone-stalled-reviews', '0', '--no-chat'], 0)
        self.launch(['--run-dir', str(run), '--no-chat'], 2)
        return run

    def test_displayed_recovery_token_refuses_a_newer_failure_before_any_settings_write(self):
        run = self.stopped_run()
        before = self.status(run)
        token = before['view']['recovery']['token']
        self.assertEqual(token, self.status(run)['view']['recovery']['token'])
        checkpoint = run / 'state.json'  # owned disposable CLI fixture only
        original = checkpoint.read_bytes()
        args = ['--run-dir', str(run), '--resume-paused', '--retry-builder', 'M1', '--no-chat']
        refused = self.launch([*args, '--expected-recovery-token', 'old', '--terra-reasoning-effort', 'low'], 2)
        self.assertIn('saved pause changed', refused.stderr)
        self.assertEqual(original, checkpoint.read_bytes())
        self.launch([*args, '--expected-recovery-token', token], 2)
        after = self.status(run)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', after['status'])
        self.assertNotEqual(token, after['view']['recovery']['token'])
        self.assertEqual(before['settings']['roles'], after['settings']['roles'])
        self.assertEqual(before['contract_token'], after['contract_token'])
        retained = checkpoint.read_bytes()
        source = (self.project / 'greet.py').read_bytes()
        refused = self.launch([*args, '--expected-recovery-token', token], 2)
        self.assertIn('saved pause changed', refused.stderr)
        self.assertEqual(retained, checkpoint.read_bytes())
        self.assertEqual(source, (self.project / 'greet.py').read_bytes())

    def test_retry_at_raw_pause_does_not_first_publish_an_operator_question(self):
        run = self.stopped_run()
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', self.status(run)['status'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--retry-builder', 'M1',
                     '--pause-after-stage', '--no-chat'], 2)
        current = self.status(run)
        self.assertEqual('PAUSED_REQUESTED', current['status'])
        self.assertEqual('terra', current['next_stage'])
        self.assertNotEqual('answer', current['view']['needs']['kind'])

    def test_explicit_retry_preserves_pins_and_requires_a_new_grant_after_failure(self):
        run = self.stopped_run()
        before = self.status(run)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', before['status'])
        original_source = (self.project / 'greet.py').read_bytes()
        args = ['--run-dir', str(run), '--resume-paused', '--no-chat']
        self.launch([*args, '--retry-builder', 'M2'], 2)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', self.status(run)['status'])
        self.launch(args, 2)
        self.assertEqual(original_source, (self.project / 'greet.py').read_bytes())
        # One explicit grant runs one more bad Builder, then stops again.
        attempt = self.launch([*args, '--retry-builder', 'M1'], 2)
        self.assertNotEqual(original_source, (self.project / 'greet.py').read_bytes(), attempt.stdout + attempt.stderr)
        self.assertEqual('PAUSED_BUILDER_RETRY_LIMIT', self.status(run)['status'])
        failed_source = (self.project / 'greet.py').read_bytes()
        self.launch(args, 2)
        self.assertEqual(failed_source, (self.project / 'greet.py').read_bytes())
        # The repaired provider now handles the same approved task correctly.
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.launch([*args, '--retry-builder', 'M1', '--pause-after-stage'], 2)
        self.assertEqual('terra', self.status(run)['next_stage'])
        self.launch([*args, '--pause-after-stage'], 2)
        self.assertEqual('sol', self.status(run)['next_stage'])
        self.launch([*args, '--pause-after-stage'], 2)
        after = self.status(run)
        self.assertEqual('PAUSED_REQUESTED', after['status'])
        self.assertEqual('astra_review', after['next_stage'])
        self.assertEqual('PASS', after['view']['evidence']['check_replay']['verdict'])
        self.assertEqual(before['contract_token'], after['contract_token'])
        # Avoiding the first paid diagnosis leaves its inherited route lazy.
        # Its later materialization must be exactly the approved planner route.
        before['settings']['roles'].setdefault('resolver', copy.deepcopy(before['settings']['roles']['astra']))
        for field in ('roles', 'limits', 'builder_retry'):
            self.assertEqual(before['settings'][field], after['settings'][field])
        valid = subprocess.run([sys.executable, 'greet.py', 'Ada'], cwd=self.project,
                               capture_output=True, text=True)
        invalid = subprocess.run([sys.executable, 'greet.py', ''], cwd=self.project,
                                 capture_output=True, text=True)
        self.assertEqual((0, 'Hello, Ada\n'), (valid.returncode, valid.stdout))
        self.assertEqual(2, invalid.returncode)


class SerialRecoveryGuards(unittest.TestCase):
    def state(self):
        state = {'status': 'PAUSED_BUILDER_RETRY_LIMIT', 'next_stage': 'terra',
                 'settings': {'builder_retry': {'enabled': True}, 'roles': {
                     'terra': {'model': 'openai/gpt-6-sol', 'model_pinned': True}}},
                 'goal_contract': {'hash': 'approved'},
                 'current_task': {'id': 't', 'milestone_id': 'M1', 'kind': 'implement'}}
        state['builder_retries'] = {recovery.policy.key(state): {'action': 'pause', 'failures': ['failure']}}
        return state

    def test_uncertain_or_wrong_milestone_attempt_is_not_changed(self):
        variants = [({'active_stage': {'stage': 'terra'}}, ['M1']),
                    ({'active_runner_check': {'stage': 'sol'}}, ['M1']),
                    ({'pending_report_repair': {'stage': 'terra'}}, ['M1']),
                    ({'parent_run': '/parent'}, ['M1']),
                    ({'status': 'PAUSED_PERMISSION'}, ['M1']),
                    ({'resolution_request': {'contract_hash': 'old'}}, ['M1']),
                    ({}, ['M2']), ({}, ['M1', 'M1'])]
        for change, selected in variants:
            with self.subTest(change=change, selected=selected):
                state = self.state(); state.update(change); original = copy.deepcopy(state)
                with self.assertRaises(ValueError):
                    recovery.request_serial_retry(state, selected)
                self.assertEqual(original, state)

    def test_pending_repair_returns_to_resolver_and_does_not_erase_failures(self):
        state = self.state(); state['current_task']['kind'] = 'validate'
        state['resolution_request'] = {'contract_hash': 'approved'}
        recovery.request_serial_retry(state, ['M1'])
        self.assertEqual('astra_resolve', state['next_stage'])
        self.assertEqual(['failure'], state['builder_retries'][recovery.policy.key(state)]['failures'])
        self.assertTrue(state['settings']['roles']['terra']['model_pinned'])
        with self.assertRaises(ValueError):
            recovery.request_serial_retry(state, ['M1'])
