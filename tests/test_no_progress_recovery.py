"""Saved no-progress escalations reconcile only a corrected finite CLI bound."""
import copy
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_resolver_human as human
import autocode_resolver_runtime as resolver
import autocode_support as support
from . import test_subprocess


class NoProgressRecoveryTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def prepare(self, next_stage='terra'):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nno\n')
        self.run, state = self.saved()
        self.launch(['--run-dir', str(self.run), '--approve-goal', state['displayed_goal']], 0)
        self.launch(['--run-dir', str(self.run), '--no-chat', '--pause-after-stage'], 2)
        _, state = self.saved()
        self.assertEqual('terra', state['next_stage'])
        state.update(next_stage=next_stage, no_progress_batches=3, automatic_recoveries_since_resume=2,
                     consecutive_timeout_recoveries=0,
                     automatic_timeout_recoveries=[{'stage': 'glm_revise', 'attempt_id': str(i)} for i in range(3)])
        support.atomic_json(self.run / 'state.json', state)
        self.launch(['--run-dir', str(self.run), '--resume-paused', '--no-chat'], 2)
        _, self.state = self.saved()
        self.state['resolver'].update(attempts={'retained': 2}, lifetime_attempts=1, diagnostic_calls=2,
                                      diagnostic_reservations=['first', 'second'])
        support.atomic_json(self.run / 'state.json', self.state)
        public = human.current(self.state)
        self.assertEqual('operational_exhaustion', public['scope'])
        origin = self.state['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']
        self.assertEqual('PAUSED_NO_PROGRESS', origin['pause_status'])
        self.assertEqual(3, origin['budget']['limit'])
        self.assertTrue(runner.goals.approved(self.state))
        self.args = ['--run-dir', str(self.run), '--no-chat']
        return public

    def respond(self, public, action='provide_information'):
        self.launch([*self.args, '--resolver-request', public['request_id'],
                     '--resolver-token', public['request_token'], '--resolver-response', action,
                     '--resolver-message', 'The finite no-progress bound will be corrected'], 0)

    def assert_retained(self, before, after):
        for key in ('goal_contract', 'user_events', 'answers', 'planning', 'history', 'stages',
                    'no_progress_batches', 'automatic_timeout_recoveries', 'automatic_recoveries_since_resume',
                    'consecutive_timeout_recoveries', 'failure_history', 'active_seconds'):
            self.assertEqual(before.get(key), after.get(key), key)
        self.assertEqual(3, after['no_progress_batches'])
        self.assertEqual(4, after['settings']['limits']['no_progress_batches'])
        self.assertTrue(runner.goals.approved(after))
        self.assertEqual('RUNNING', after['status'])
        self.assertNotIn(human.PUBLIC, after)
        self.assertNotIn('budget_extensions', after['resolver'])
        self.assertNotIn('recovery_review_grants', after.get('planning', {}))
        for key in ('attempts', 'lifetime_attempts', 'diagnostic_calls', 'diagnostic_reservations'):
            self.assertEqual(before['resolver'].get(key), after['resolver'].get(key), key)

    def test_cli_finite_increase_retires_live_request_without_new_allowance(self):
        public = self.prepare()
        self.launch([*self.args, '--resume-paused', '--no-progress-limit', '4', '--unit', 'autoplanner'], 0)
        _, resumed = self.saved()
        self.assert_retained(self.state, resumed)
        self.assertEqual('superseded', resumed['resolver']['human_escalations'][public['request_id']]['status'])
        self.assertFalse((self.project / 'greet.py').exists())
        # Only the offline provider runs, through the normal approved build route.
        self.launch(self.args, 0)
        _, finished = self.saved()
        self.assertEqual('TASK_COMPLETE', finished['status'])
        self.assertEqual(self.state['goal_contract'], finished['goal_contract'])
        self.assertEqual(self.state['automatic_timeout_recoveries'], finished['automatic_timeout_recoveries'])

    def test_cli_already_saved_increase_reconciles_original_request(self):
        self.prepare()
        self.launch([*self.args, '--no-progress-limit', '4'], 2)
        _, changed = self.saved()
        self.assertEqual(4, changed['settings']['limits']['no_progress_batches'])
        self.launch([*self.args, '--resume-paused', '--unit', 'autoplanner'], 0)
        self.assert_retained(changed, self.saved()[1])

    def test_cli_human_response_before_change_does_not_republish_corrected_cause(self):
        public = self.prepare()
        self.respond(public)
        _, answered = self.saved()
        self.assertEqual('PAUSED_NO_PROGRESS', answered['status'])
        before = (self.run / 'state.json').read_bytes()
        self.launch([*self.args, '--resume-paused'], 2)
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.launch([*self.args, '--no-progress-limit', '4'], 2)
        _, corrected = self.saved()
        self.assertEqual('PAUSED_NO_PROGRESS', corrected['status'])
        self.assertNotIn(human.PUBLIC, corrected)
        self.launch([*self.args, '--resume-paused', '--unit', 'autoplanner'], 0)
        _, resumed = self.saved()
        self.assert_retained(answered, resumed)
        self.assertEqual(answered['resolver']['human_responses'], resumed['resolver']['human_responses'])
        self.assertEqual(answered['resolver']['human_response_resolutions'], resumed['resolver']['human_response_resolutions'])

    def test_cli_current_trial_shape_republished_request_at_four_count_three(self):
        original_request = self.prepare(next_stage='orchestrator')
        self.respond(original_request)
        _, answered = self.saved()
        # A pre-fix launch could publish another ask after the response and bound
        # update, despite the execution guard already admitting the retained count.
        state = copy.deepcopy(answered)
        state['settings']['limits']['no_progress_batches'] = 4
        state['settings']['budget_origins']['no_progress_batches'] = 'user_explicit'
        human.supersede_operational(state, 'fixture represents the pre-fix republication')
        state.update(status='PAUSED_NO_PROGRESS', pending_questions=[])
        # Recreate the historical, erroneously republished request, not a fresh
        # exhaustion (the corrected runtime must refuse to publish that cause).
        prior = state['resolver']['human_escalations'][original_request['request_id']]['identity']['proposal']
        proposal = copy.deepcopy(prior)
        proposal['origin']['budget'].update(limit=4, origin='user_explicit')
        receipt = resolver._operational_receipt(state, self.run, 'hold', 'Retained pre-fix no-progress hold', {})
        human.queue(state, 'operational_exhaustion', proposal['origin'], request=proposal['request'],
                    evidence={'resolver_receipt_id': receipt}, next_stage='orchestrator')
        runner.write_json(self.run / 'state.json', state)
        public = human.current(state)
        self.assertEqual('operational_exhaustion', public['scope'])
        before = (self.run / 'state.json').read_bytes()
        self.launch([*self.args, '--status'], 0)
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.launch([*self.args, '--resume-paused', '--unit', 'autoplanner'], 0)
        _, resumed = self.saved()
        self.assert_retained(state, resumed)
        self.assertEqual('orchestrator', resumed['next_stage'])
        self.assertEqual(answered['resolver']['human_responses'], resumed['resolver']['human_responses'])

    def test_cli_unchanged_disabled_or_still_exhausted_bounds_do_not_admit(self):
        public = self.prepare()
        original = copy.deepcopy(self.state)
        for limit, count in ((3, 3), (0, 3), (4, 4), (4, 5), (2, 3)):
            with self.subTest(limit=limit, count=count):
                state = copy.deepcopy(original)
                state['no_progress_batches'] = count
                support.atomic_json(self.run / 'state.json', state)
                self.launch([*self.args, '--resume-paused', '--no-progress-limit', str(limit),
                             '--unit', 'autoplanner'], 2)
                _, blocked = self.saved()
                self.assertEqual('pending', blocked['resolver']['human_escalations'][public['request_id']]['status'])
                self.assertEqual(count, blocked['no_progress_batches'])
                self.assertEqual(original['goal_contract'], blocked['goal_contract'])
                self.assertEqual(original['history'], blocked['history'])
                self.assertFalse((self.project / 'greet.py').exists())

    def test_reconciliation_rejects_other_changes_and_preserves_state_on_failure(self):
        self.prepare()
        original = copy.deepcopy(self.state)
        original['settings']['limits']['no_progress_batches'] = 4
        original['settings']['budget_origins']['no_progress_batches'] = 'user_explicit'
        for case in ('model', 'approval', 'active', 'repair', 'question', 'pause', 'intervention', 'origin'):
            with self.subTest(case=case):
                state = copy.deepcopy(original)
                if case == 'model':
                    state['settings']['roles']['astra']['model'] = 'different-model'
                elif case == 'approval':
                    state['goal_contract']['approval_status'] = 'draft'
                elif case == 'active':
                    state['active_stage'] = {'stage': 'terra'}
                elif case == 'repair':
                    state['pending_report_repair'] = {'attempts': 2}
                elif case == 'question':
                    state['pending_questions'].append({'id': 'Q-other', 'question': 'Change scope?'})
                elif case == 'pause':
                    state['pause_intent'] = {'at': 'now'}
                elif case == 'origin':
                    state['settings']['budget_origins']['no_progress_batches'] = 'resolver_delegated'
                before = copy.deepcopy(state)
                with patch.object(runner.interventions, 'pending', return_value=[{}] if case == 'intervention' else []):
                    self.assertFalse(resolver.reconcile_no_progress_request(runner, state, self.run))
                self.assertEqual(before, state)

    def test_other_operational_cause_is_not_a_no_progress_recovery(self):
        self.prepare()
        state = copy.deepcopy(self.state)
        human.supersede_operational(state, 'fixture changes the operational cause')
        state['settings']['limits']['no_progress_batches'] = 4
        state['settings']['budget_origins']['no_progress_batches'] = 'user_explicit'
        state['status'] = 'PAUSED_RATE_LIMIT'
        resolver.record_operational_exhaustion(runner, state, self.run,
                                               support.Paused('PAUSED_RATE_LIMIT', 'Provider rate limit'))
        runner.write_json(self.run / 'state.json', state)
        before = copy.deepcopy(state)
        self.assertFalse(resolver.reconcile_no_progress_request(runner, state, self.run))
        self.assertEqual(before, state)


if __name__ == '__main__':
    unittest.main()
