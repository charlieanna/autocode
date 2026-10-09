"""Human decisions become actionable only at the resolver publication boundary."""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode as runner
import autocode_args
import autocode_resolver_human as human
import autocode_run_actions as run_actions
import autocode_support as support

from . import test_resolver_human, test_subprocess


class HumanPublicationTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    queue_question = test_resolver_human.ResolverHumanTests.queue_question
    request = test_resolver_human.ResolverHumanTests.request
    publish_operational = test_resolver_human.ResolverHumanTests.publish_operational

    def test_resume_companions_recognize_only_a_current_operational_pause(self):
        run = Path(self.state['run_dir'])
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, self.state, run, support.Paused('PAUSED_TIMEOUT_RECOVERY', 'Recovery exhausted')))
        runner.write_json(run / 'state.json', self.state)
        self.assertEqual('WAITING_FOR_USER', self.state['status'])
        argv = ['resume', '--run-dir', str(run)]
        before = (run / 'state.json').read_bytes()
        for flags in (['--grant-recovery', '1'], ['--retry-failed-stage'], ['--retry-builder', 'M2'],
                      ['--retry-report', '001/sol-01'], ['--diagnose-failed-stage'],
                      ['--accept-transport-change'], ['--expected-recovery-token', 'inspected'],
                      ['--max-seconds', '60']):
            with self.subTest(flags=flags):
                args, _ = autocode_args.parse(None, [*argv, *flags], runner.DEFAULT_ROLE_MODELS)
                self.assertTrue(args.resume_paused)
        for flags in ([], ['--status'], ['--feedback', 'smaller']):
            with self.subTest(flags=flags):
                args, _ = autocode_args.parse(None, [*argv, *flags], runner.DEFAULT_ROLE_MODELS)
                self.assertFalse(args.resume_paused)
        self.assertEqual(before, (run / 'state.json').read_bytes(), 'parsing must not change the receipt')

        self.state['active_seconds'] += 1
        support.atomic_json(run / 'state.json', self.state)
        args, _ = autocode_args.parse(None, [*argv, '--max-seconds', '60'], runner.DEFAULT_ROLE_MODELS)
        self.assertFalse(args.resume_paused, 'a stale operational request cannot authorize the shortcut')
        self.queue_question()
        runner.write_json(run / 'state.json', self.state)
        self.assertEqual('clarification', human.current(self.state)['scope'])
        args, _ = autocode_args.parse(None, [*argv, '--max-seconds', '60'], runner.DEFAULT_ROLE_MODELS)
        self.assertFalse(args.resume_paused, 'a valid ordinary question is not an operational pause')

    def test_recovery_grant_validates_published_request_before_resume_epoch_changes(self):
        import autocode_args
        self.state.update(status='PAUSED_TIMEOUT_RECOVERY', next_stage='investigate_bug',
                          automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3)
        self.state['resolver'] = {'attempts': {'external-directory': 3}}
        run = Path(self.state['run_dir'])
        runner.resolver_runtime.record_operational_exhaustion(
            runner, self.state, run, support.Paused('PAUSED_TIMEOUT_RECOVERY', 'Recovery exhausted'))
        runner.write_json(run / 'state.json', self.state)
        published = human.current(self.state)
        self.assertIsNotNone(published)
        args = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS).parse_args(
            ['--run-dir', str(run), '--resume-paused', '--grant-recovery', '1', '--no-chat'])
        fake_runner = Mock(wraps=runner)
        fake_runner.ReportRepairQueued = runner.ReportRepairQueued
        class GrantApplied(Exception):
            pass
        fake_runner.prepare_abandoned_completion_revalidation.side_effect = GrantApplied
        with self.assertRaises(GrantApplied):
            run_actions.handle(fake_runner, args, None, self.state, run / 'state.json', run, self.root)
        self.assertEqual(2, self.state['automatic_recoveries_since_resume'])
        self.assertEqual(1, len(self.state['recovery_grants']))
        self.assertEqual(published['request_id'], self.state['recovery_grants'][0]['request_id'])
        self.assertEqual(3, self.state['resolver']['lifetime_attempts'])
        self.assertEqual('superseded',
                         self.state['resolver']['human_escalations'][published['request_id']]['status'])
        fake_runner.run_role.assert_not_called()

    def test_generic_answer_cannot_reclassify_operational_question_as_new_requirements(self):
        self.contract()
        published = self.publish_operational()
        question = published['questions'][0]['id']
        before = copy.deepcopy(self.state)
        for delegated in (False, True):
            with self.subTest(delegated=delegated), self.assertRaisesRegex(ValueError, 'Operational'):
                runner.goals.answer(self.state, question, 'Extend the execution limit', delegated=delegated)
            self.assertEqual(before, self.state)

    def test_chat_operational_guidance_preserves_contract_and_all_exhaustion_counters(self):
        self.contract()
        self.publish_operational()
        before = copy.deepcopy(self.state)
        with patch('builtins.input', return_value='The environment was repaired; do not change scope'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(runner.chat_checkpoint(self.state))
        for key in ('goal_contract', 'planning', 'stages', 'settings', 'next_stage', 'failure_history',
                    'active_seconds', 'automatic_recoveries_since_resume'):
            self.assertEqual(before.get(key), self.state.get(key), key)
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', self.state['status'])
        self.assertIsNone(human.current(self.state))
        self.assertNotIn('pending_human_response', self.state['resolver'])
        self.assertEqual('hold', next(iter(self.state['resolver']['human_response_resolutions'].values()))['action'])

    def test_state_persistence_authorizes_before_progress_publication(self):
        self.queue_question()
        self.assertEqual([], self.state['pending_questions'])
        path = Path(self.state['run_dir']) / 'state.json'
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            runner.write_json(path, self.state)
        saved = support.read(path)
        self.assertEqual('WAITING_FOR_USER', saved['status'])
        self.assertEqual('resolver', human.current(saved)['issuer'])
        self.assertEqual([self.question], saved['pending_questions'])
        self.assertIn('AutoResolver:', stderr.getvalue())
        self.assertNotIn('Planner:', stderr.getvalue())
        self.assertEqual('assistant', saved['progress_messages'][-1]['role'])
        self.assertEqual('AutoResolver', saved['progress_messages'][-1]['speaker'])

    def test_status_does_not_mint_authority_for_a_legacy_question(self):
        self.state.update(status='WAITING_FOR_USER', pending_questions=[self.question], sessions={})
        path = Path(self.state['run_dir']) / 'state.json'
        support.atomic_json(path, self.state)
        before = path.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(Path(runner.__file__)),
            '--workspace', str(self.root), '--run-dir', str(path.parent), '--status'],
            capture_output=True, text=True, timeout=20, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['human_request_authorized'])
        self.assertEqual('RESOLVER_PENDING', payload['status'])
        self.assertEqual('WAITING_FOR_USER', payload['saved_status'])
        self.assertEqual([], payload['pending_questions'])
        self.assertEqual(before, path.read_bytes())

    def test_operational_exhaustion_is_published_by_resolver_not_overwritten(self):
        self.state.update(status='PAUSED_TIMEOUT_RECOVERY', next_stage='astra_challenge')
        run = Path(self.state['run_dir'])
        error = support.Paused('PAUSED_TIMEOUT_RECOVERY', 'Bounded recovery exhausted')
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, run, error))
        self.assertEqual('RESOLVER_PENDING', self.state['status'])
        with contextlib.redirect_stderr(io.StringIO()):
            runner.write_json(run / 'state.json', self.state)
        saved = support.read(run / 'state.json')
        self.assertEqual('WAITING_FOR_USER', saved['status'])
        public = human.current(saved)
        self.assertEqual('operational_exhaustion', public['scope'])
        self.assertEqual('resolver', public['issuer'])
        self.assertIn('cannot continue safely', public['request']['impact'])
        self.assertEqual(3, saved['failure_history']['failure']['count'])

    def test_worker_cannot_publish_a_question_independently(self):
        self.queue_question()
        self.state['parent_run'] = '/parent/run'
        run = Path(self.state['run_dir'])
        with contextlib.redirect_stderr(io.StringIO()):
            runner.write_json(run / 'state.json', self.state)
        saved = support.read(run / 'state.json')
        self.assertFalse(human.projection(saved)['human_request_authorized'])
        self.assertEqual([], saved['pending_questions'])
        self.assertIn(human.PRIVATE, saved)

    def test_answered_operational_request_allows_explicit_stage_abandonment(self):
        import autocode_args
        published = self.publish_operational()
        human.respond_operational(self.state, published['request_id'], published['request_token'],
                                  'provide_information', 'Inspected the uncertain attempt')
        human.review_operational_response(self.state)
        self.assertTrue(human.response_holds_current_frontier(self.state))
        args = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS).parse_args(
            ['--run-dir', self.state['run_dir'], '--abandon-stage', '001/terra-01', '--no-chat'])
        fake_runner = Mock()
        fake_runner.abandon_stage.side_effect = lambda state, *_, **__: state.update(
            status='PAUSED_STAGE_ABANDONED', stop_reason='Stage set aside')
        with patch.object(run_actions.dependency, 'apply', return_value=None), \
                patch.object(run_actions.resolver_runtime, 'record_operational_exhaustion', return_value=True) as publish:
            result = run_actions.handle(fake_runner, args, None, self.state,
                                        Path(self.state['run_dir']) / 'state.json',
                                        Path(self.state['run_dir']), self.root)
        self.assertEqual(0, result)
        fake_runner.abandon_stage.assert_called_once()
        publish.assert_not_called()


class HumanResponseCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def stopped_operational_checkpoint(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        run, stopped = self.saved()
        # Publish a fresh, reconciled operational checkpoint. The test then
        # exercises only answering it, not uncertain-provider recovery.
        human.supersede_operational(stopped, 'Fixture reconciled its provider attempt')
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, stopped, run, support.Paused('PAUSED_TIME_LIMIT', 'Fixture execution allowance exhausted')))
        runner.write_json(run / 'state.json', stopped)
        return run, self.saved()[1]

    def timeout_exhausted_checkpoint(self, *, publish_request=True):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        run, stopped = self.saved()
        stopped.update(status='PAUSED_TIMEOUT_RECOVERY', stop_reason='Automatic recovery budget exhausted',
                       automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3)
        stopped['automatic_timeout_recoveries'] = [
            {'stage': 'terra', 'timeout_reason': 'idle watchdog'},
            {'stage': 'terra', 'timeout_reason': 'idle watchdog'},
            {'stage': 'terra', 'timeout_reason': 'idle watchdog'}]
        if publish_request:
            self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
                runner, stopped, run, support.Paused('PAUSED_TIMEOUT_RECOVERY', 'Automatic recovery budget exhausted')))
        runner.write_json(run / 'state.json', stopped)
        return run, self.saved()[1]

    def test_abandon_stage_retires_its_request_and_explicit_resume_dispatches(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        published = human.current(paused)
        self.assertIsNotNone(published)
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        _, abandoned = self.saved()
        self.assertEqual('PAUSED_STAGE_ABANDONED', abandoned['status'])
        self.assertEqual([], abandoned['pending_questions'])
        self.assertEqual('superseded',
                         abandoned['resolver']['human_escalations'][published['request_id']]['status'])
        stages_before = len(abandoned['stages'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        _, resumed = self.saved()
        self.assertNotEqual('PAUSED_UNANSWERED_QUESTION', resumed['status'])
        self.assertGreater(len(resumed['stages']), stages_before)

    def test_real_cli_stale_request_response_rebinds_in_one_invocation(self):
        run, exhausted = self.timeout_exhausted_checkpoint()
        published = human.current(exhausted)
        self.assertIsNotNone(published)
        path = run / 'state.json'
        saved = json.loads(path.read_text())
        saved['active_seconds'] = saved.get('active_seconds', 0) + 1
        path.write_text(json.dumps(saved))
        result = self.launch(['--run-dir', str(run), '--resolver-request', published['request_id'],
                              '--resolver-response', 'provide_information', '--resolver-message', 'Cause fixed',
                              '--resolver-token', published['request_token']], 0)
        self.assertIn('AutoResolver received the response', result.stdout)
        _, answered = self.saved()
        self.assertIsNone(human.current(answered))
        resolution = answered['resolver']['human_response_resolutions']
        self.assertEqual(1, len(resolution))
        reborn = answered['resolver']['human_escalations'][published['request_id']]
        self.assertEqual('superseded', reborn['status'])
        self.assertIn('Re-bound', reborn['superseded_reason'])
        self.assertEqual('user_cli', next(iter(resolution.values()))['response']['actor'])
        self.assertEqual(len(exhausted['stages']), len(answered['stages']),
                         'recording a response must not launch a provider')

    def test_stale_rebind_rejects_forged_tokens_and_newer_requests(self):
        run, exhausted = self.timeout_exhausted_checkpoint()
        published = human.current(exhausted)
        path = run / 'state.json'
        saved = json.loads(path.read_text())
        saved['active_seconds'] = saved.get('active_seconds', 0) + 1
        path.write_text(json.dumps(saved))
        before = path.read_bytes()
        result = self.launch(['--run-dir', str(run), '--resolver-request', published['request_id'],
                              '--resolver-response', 'provide_information', '--resolver-message', 'no',
                              '--resolver-token', published['request_token'][:-4] + 'beef'], 2)
        self.assertIn('requires the exact current', result.stderr)
        self.assertEqual(before, path.read_bytes())

    def test_real_cli_grant_recovery_resumes_an_exhausted_timeout_run(self):
        run, exhausted = self.timeout_exhausted_checkpoint()
        published = human.current(exhausted)
        self.assertEqual('operational_exhaustion', published['scope'])
        self.assertIn('--grant-recovery', published['request']['decision_needed'])
        stages_before = len(exhausted['stages'])
        history = len(exhausted['automatic_timeout_recoveries'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        _, held = self.saved()
        self.assertEqual(stages_before, len(held['stages']))
        self.assertEqual(3, held['automatic_recoveries_since_resume'])
        self.assertIsNotNone(human.current(held))
        self.launch(['--run-dir', str(run), '--resume-paused', '--grant-recovery', '1', '--no-chat'], 2)
        _, granted = self.saved()
        self.assertGreater(len(granted['stages']), stages_before)
        self.assertEqual(2, granted['automatic_recoveries_since_resume'])
        self.assertEqual(0, granted['consecutive_timeout_recoveries'])
        self.assertEqual(history, len(granted['automatic_timeout_recoveries']))
        events = [event for event in granted['user_events'] if event.get('kind') == 'recovery_grant']
        self.assertEqual(1, len(events))
        self.assertEqual(1, events[0]['amount'])
        self.assertEqual(published['request_id'], events[0]['request_id'])
        self.assertEqual('superseded',
                         granted['resolver']['human_escalations'][published['request_id']]['status'])
        self.assertNotEqual('PAUSED_TIMEOUT_RECOVERY', granted['status'])

    def test_grant_recovery_requires_a_timeout_exhausted_pause(self):
        run, stopped = self.stopped_operational_checkpoint()
        before = (run / 'state.json').read_bytes()
        for resume in ('--resume-paused', 'resume'):
            with self.subTest(resume=resume):
                result = self.launch([resume, '--run-dir', str(run), '--grant-recovery', '1', '--no-chat'], 2)
                self.assertIn('requires a run paused for exhausted timeout recovery', result.stderr)
                self.assertEqual(before, (run / 'state.json').read_bytes())

    def test_resume_word_grants_recovery_for_a_published_timeout_pause(self):
        run, paused = self.timeout_exhausted_checkpoint()
        self.assertEqual('WAITING_FOR_USER', paused['status'])
        probe = self.root / 'resume-recovery-launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        held = self.launch(['resume', '--run-dir', str(run), '--no-chat'], 2)
        self.assertNotIn('Recovery grant recorded', held.stdout)
        self.assertFalse(probe.exists(), 'bare resume cannot grant recovery credit')
        result = self.launch(['resume', '--run-dir', str(run), '--grant-recovery', '1', '--no-chat'], 2)
        self.assertIn('Recovery grant recorded: 1', result.stdout)
        self.assertTrue(probe.exists(), 'the authorized recovery must reach the provider')

    def test_grant_accepts_corrected_timeouts_in_the_same_cli_invocation(self):
        run, exhausted = self.timeout_exhausted_checkpoint()
        published = human.current(exhausted)
        probe = self.root / 'corrected-timeout-launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        result = self.launch(['--run-dir', str(run), '--resume-paused', '--grant-recovery', '1',
                             '--max-stage-seconds', '300', '--max-idle-seconds', '120', '--no-chat'], 2)
        self.assertIn('Recovery grant recorded: 1', result.stdout)
        self.assertTrue(probe.exists(), 'the corrected run must launch its next provider')
        saved = self.saved()[1]
        self.assertEqual(exhausted['automatic_timeout_recoveries'], saved['automatic_timeout_recoveries'])
        self.assertEqual(published['request_id'], saved['recovery_grants'][-1]['request_id'])
        self.assertEqual(2, saved['recovery_grants'][-1]['remaining_count'])

    def test_changing_limits_cannot_authorize_an_already_stale_grant(self):
        run, _ = self.timeout_exhausted_checkpoint()
        (self.project / 'greet.py').write_text('# a different source after the request was issued\n')
        probe = self.root / 'stale-grant-launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        result = self.launch(['--run-dir', str(run), '--resume-paused', '--grant-recovery', '1',
                             '--max-stage-seconds', '300', '--max-idle-seconds', '120', '--no-chat'], 2)
        self.assertIn('requires a run paused for exhausted timeout recovery', result.stderr)
        self.assertFalse(probe.exists(), 'a stale source binding must not launch a provider')
        self.assertFalse(self.saved()[1].get('recovery_grants'))

    def test_grant_recovery_resumes_in_one_invocation_from_a_bare_timeout_pause(self):
        run, paused = self.timeout_exhausted_checkpoint(publish_request=False)
        self.assertIsNone(human.current(paused))
        stages_before = len(paused['stages'])
        result = self.launch(['--run-dir', str(run), '--resume-paused', '--grant-recovery', '1', '--no-chat'], 2)
        self.assertIn('Recovery grant recorded', result.stdout)
        _, granted = self.saved()
        self.assertGreater(len(granted['stages']), stages_before)
        self.assertEqual(2, granted['automatic_recoveries_since_resume'])
        self.assertEqual(1, len([event for event in granted['user_events']
                                 if event.get('kind') == 'recovery_grant']))

    def test_real_cli_generic_answer_rejects_operational_request_without_changing_approval(self):
        run, paused = self.stopped_operational_checkpoint()
        published = human.current(paused)
        question = published['questions'][0]['id']
        before = (run / 'state.json').read_bytes()
        result = self.launch(['--run-dir', str(run), '--resolver-token', published['request_token'],
                             '--answer', question + '=Extend the execution limit'], 2)
        self.assertIn('Use --resolver-response', result.stderr)
        self.assertEqual(before, (run / 'state.json').read_bytes())

    def test_real_cli_chat_operational_response_does_not_start_discovery_or_another_provider(self):
        run, before = self.stopped_operational_checkpoint()
        result = self.launch(['--run-dir', str(run), '--chat'], 2,
                             answers='Provider access restored; keep the approved scope\n')
        run, saved = self.saved()
        self.assertIn('No retry, approval, permission or budget increase', result.stdout)
        self.assertTrue(runner.goals.approved(saved))
        self.assertEqual(before['goal_contract'], saved['goal_contract'])
        self.assertEqual(before['stages'], saved['stages'])
        self.assertNotEqual('astra_discovery', saved['next_stage'])
        self.assertIsNone(human.current(saved))
        resolutions = saved['resolver']['human_response_resolutions']
        self.assertEqual(1, len(resolutions))
        self.assertEqual('hold', next(iter(resolutions.values()))['action'])
        stages = copy.deepcopy(saved['stages'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        held = self.saved()[1]
        self.assertEqual(saved['goal_contract'], held['goal_contract'])
        self.assertEqual(stages, held['stages'])

    def test_explicit_limit_change_preserves_approval_and_does_not_launch_a_provider(self):
        run, paused = self.stopped_operational_checkpoint()
        limit = int(paused['settings']['limits'].get('max_seconds') or 0) + 3600
        self.launch(['--run-dir', str(run), '--max-seconds', str(limit), '--show-goal'], 0)
        changed = self.saved()[1]
        for key in ('goal_contract', 'stages', 'planning', 'failure_history', 'active_seconds', 'next_stage'):
            self.assertEqual(paused.get(key), changed.get(key), key)
        self.assertTrue(runner.goals.approved(changed))
        self.assertEqual(limit, changed['settings']['limits']['max_seconds'])
        self.assertEqual(limit, changed['configuration_changes'][-1]['selected']['limits']['max_seconds'])
        self.assertIsNone(human.current(changed))

    def test_real_cli_operational_response_does_not_replan_or_reset_limits(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        published = human.current(paused)
        self.assertIsNotNone(published)
        self.assertEqual('operational_exhaustion', published['scope'])
        path = run / 'state.json'
        before = path.read_bytes()
        args = ['--run-dir', str(run), '--resolver-request', published['request_id'],
                '--resolver-response', 'provide_information', '--resolver-message', 'Provider issue inspected']
        self.launch([*args, '--resolver-token', 'wrong'], 2)
        self.assertEqual(before, path.read_bytes())
        self.launch([*args, '--resolver-token', published['request_token']], 0)
        _, answered = self.saved()
        self.assertEqual(paused['goal_contract'], answered['goal_contract'])
        self.assertEqual(paused.get('failure_history'), answered.get('failure_history'))
        self.assertEqual(paused.get('planning'), answered.get('planning'))
        self.assertEqual(paused['active_seconds'], answered['active_seconds'])
        self.assertEqual(paused['stages'], answered['stages'])
        self.assertIsNone(human.current(answered))
        self.assertNotIn('pending_human_response', answered['resolver'])
        resolution = answered['resolver']['human_response_resolutions'][published['request_id']]
        self.assertEqual('user_cli', resolution['response']['actor'])
        self.assertEqual('Provider issue inspected', resolution['response']['text'])
        count = len(answered['resolver']['human_escalations'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        _, held = self.saved()
        self.assertEqual(count, len(held['resolver']['human_escalations']))
        self.assertEqual(answered['stages'], held['stages'])
        self.assertEqual(answered.get('failure_history'), held.get('failure_history'))
