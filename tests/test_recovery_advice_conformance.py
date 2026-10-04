"""Every recovery action a stop advertises must be accepted there, and one
documented action must move the run again (#288/#301).

Real CLI processes with an explicitly fake provider; no sleeps, no live models.
Checkpoints mirror the pause classes reported live: exhausted automatic
timeout recovery, an explicit user time cap, and exhausted AutoResolver
operational-recovery attempts. A repeated OpenCode external_directory denial
is driven through the public CLI by the scripted adversarial provider.
"""
import json
import subprocess
import re
from pathlib import Path
import unittest

from . import test_subprocess
from scenarios.harness.adversarial import AdversarialCase
from scenarios.harness.driver import default_autocode
from scenarios.harness.processes import run_cli
import autocode as runner
import autocode_resolver_human as human
import autocode_support as support


ADVERTISED_FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")


class RecoveryAdviceConformanceTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def base_checkpoint(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        return run, self.saved()[1]

    def publish(self, stopped, status, stop_reason):
        stopped.update(status=status, stop_reason=stop_reason)
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, stopped, self.run, support.Paused(status, stop_reason)))
        runner.write_json(self.run / 'state.json', stopped)
        return self.saved()[1]

    def timeout_exhausted_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        stopped.update(automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3)
        stopped['automatic_timeout_recoveries'] = [
            {'stage': 'terra', 'timeout_reason': 'idle watchdog'} for _ in range(3)]
        return self.publish(stopped, 'PAUSED_TIMEOUT_RECOVERY',
                            'Automatic recovery budget exhausted; no further provider will launch.')

    def explicit_time_cap_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        stopped.setdefault('settings', {}).setdefault('limits', {})['max_seconds'] = 7200
        stopped['settings'].setdefault('budget_origins', {})['max_seconds'] = 'user_explicit'
        stopped['active_seconds'] = 7300
        return self.publish(stopped, 'PAUSED_TIME_LIMIT',
                            'Saved active-time limit reached at stage boundary')

    def attempt_exhausted_checkpoint(self):
        self.run, stopped = self.base_checkpoint()
        return self.publish(stopped, 'PAUSED_RESOLVER_OPERATIONAL',
                            'AutoResolver exhausted its recorded operational recoveries')

    def advertised_commands(self, state):
        published = human.current(state)
        surfaces = [state.get('stop_reason') or '']
        if published:
            request = published.get('request') or {}
            surfaces.append(request.get('decision_needed') or '')
            surfaces.extend(request.get('options') or [])
            surfaces.extend((q.get('question') or '') for q in published.get('questions') or [])
        flags = set()
        for text in surfaces:
            flags.update(ADVERTISED_FLAGS.findall(text))
        return flags, published

    def progress(self, before, probe=None):
        """A resume made progress when a provider launched or the saved run moved."""
        _, after = self.saved()
        moved = (after.get('status') != before.get('status')
                 or len(after.get('stages', [])) != len(before.get('stages', [])))
        return moved or (probe is not None and probe.exists())

    def test_timeout_exhaustion_advertises_only_accepted_actions(self):
        state = self.timeout_exhausted_checkpoint()
        flags, published = self.advertised_commands(state)
        self.assertIn('--grant-recovery', flags)
        self.assertNotIn('--retry-failed-stage', flags)
        result = self.launch(['--run-dir', str(self.run), '--resume-paused',
                              '--grant-recovery', '1', '--no-chat'], 2)
        self.assertNotIn('Input rejected', result.stderr)
        self.assertNotIn('requires a run paused for exhausted timeout recovery', result.stderr)
        self.assertTrue(self.progress(state), 'the advertised grant must move the run')

    def test_explicit_time_cap_never_advertises_grant_and_bound_change_resumes_once(self):
        state = self.explicit_time_cap_checkpoint()
        flags, published = self.advertised_commands(state)
        self.assertNotIn('--grant-recovery', flags,
                         'an explicit user cap must not advertise the timeout-only grant')
        result = self.launch(['--run-dir', str(self.run), '--resume-paused',
                              '--grant-recovery', '1', '--no-chat'], 2)
        self.assertIn('requires a run paused for exhausted timeout recovery', result.stderr)
        raised = int(state['settings']['limits']['max_seconds']) + 3600
        result = self.launch(['--run-dir', str(self.run), '--resume-paused',
                              '--max-seconds', str(raised), '--no-chat'], 2)
        self.assertNotIn('retained the human guidance', result.stdout,
                         'raising the exhausted bound must supersede in the same invocation')
        self.assertNotIn('Input rejected', result.stderr)
        self.assertTrue(self.progress(state),
                        'raising the exhausted bound must move the run in one invocation')

    def test_attempt_exhaustion_advertises_only_accepted_actions(self):
        state = self.attempt_exhausted_checkpoint()
        state.setdefault('automatic_permission_recoveries', []).extend(
            {'stage': 'terra', 'instruction': 'workspace paths only'} for _ in range(3))
        runner.write_json(self.run / 'state.json', state)
        flags, published = self.advertised_commands(state)
        self.assertNotIn('--grant-recovery', flags)
        # Since #301 this stop advertises its one fresh attempt. The injected history above makes the
        # published request stale, so its acceptance is checked on an intact checkpoint below.
        self.assertIn('--retry-failed-stage', flags)
        for flag in sorted(flags):
            if flag in ('--resolver-response', '--retry-failed-stage'):
                continue  # answered through the request's own options, not a bare CLI retry
            with self.subTest(flag=flag):
                self.assertNotIn('requires', self.describe_rejection(flag))

    def resume(self, *flags):
        """Run --resume-paused with ``flags``; return (exit code, whether a provider launched, saved state)."""
        probe = self.root / f'launch-{len(list(self.root.glob("launch-*")))}.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        result = subprocess.run([*self.entry, '--workspace', str(self.project), '--run-dir', str(self.run),
                                 '--resume-paused', *flags, '--no-chat'],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        return result.returncode, probe.exists(), self.saved()[1]

    def paused_for(self, state):
        published = human.current(state)
        if published:
            entry = state['resolver']['human_escalations'][published['request_id']]
            return entry['identity']['proposal']['origin'].get('pause_status')
        return state.get('status')

    def test_an_unrelated_limit_change_never_releases_the_time_cap(self):
        # #379: --max-stage-seconds is a different bound from the exhausted --max-seconds. The settings
        # change rebound the published request into a legacy blocker, and the generic resume launched a stage.
        state = self.explicit_time_cap_checkpoint()
        code, launched, after = self.resume('--max-stage-seconds', '1200')
        self.assertFalse(launched, 'an unrelated limit must not admit a provider past the exhausted time cap')
        self.assertEqual(2, code)
        self.assertEqual('PAUSED_TIME_LIMIT', self.paused_for(after))
        self.assertEqual(1200, after['settings']['limits']['stage_timeout_seconds'], 'the unrelated change itself is kept')
        code, launched, after = self.resume()
        model_stages = [row['stage'] for row in after['stages'][len(state['stages']):] if not row.get('runner_owned')]
        self.assertEqual([], model_stages, 'no model may run past the exhausted cap, AutoResolver included')
        self.assertEqual('PAUSED_TIME_LIMIT', self.paused_for(after))
        raised = int(state['settings']['limits']['max_seconds']) + 3600
        code, launched, after = self.resume('--max-seconds', str(raised))
        self.assertTrue(launched or after['status'] != 'PAUSED_TIME_LIMIT',
                        'raising the exhausted bound still resumes afterwards')

    def test_restating_a_saved_cap_with_headroom_resumes_after_a_consumed_response(self):
        # #378: the user answered the time-cap request (provide_information consumes it), then saved a raised
        # cap. Restating that cap with --resume-paused changed no setting, so it set no acknowledgment, and the
        # consumed response held the run: no command could resume it although the cap left headroom.
        state = self.explicit_time_cap_checkpoint()
        published = human.current(state)
        self.launch(['--run-dir', str(self.run), '--resolver-request', published['request_id'],
                     '--resolver-token', published['request_token'], '--resolver-response', 'provide_information',
                     '--resolver-message', 'One more hour is authorized', '--no-chat'], 0)
        raised = int(state['settings']['limits']['max_seconds']) + 3600
        result = subprocess.run([*self.entry, '--workspace', str(self.project), '--run-dir', str(self.run),
                                 '--max-seconds', str(raised), '--no-chat'],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(raised, self.saved()[1]['settings']['limits']['max_seconds'], result.stdout + result.stderr)
        code, launched, after = self.resume('--max-seconds', str(raised))
        self.assertTrue(launched, 'restating the saved cap that leaves headroom must resume the time-limit pause')

    def test_restating_an_exhausted_cap_stays_paused(self):
        state = self.explicit_time_cap_checkpoint()
        code, launched, after = self.resume('--max-seconds', str(state['settings']['limits']['max_seconds']))
        self.assertFalse(launched, 'a cap without headroom is not authority to continue')
        self.assertEqual('PAUSED_TIME_LIMIT', self.paused_for(after))

    def test_attempt_exhaustion_advertises_one_fresh_attempt_that_moves_the_run(self):
        state = self.attempt_exhausted_checkpoint()
        flags, published = self.advertised_commands(state)
        self.assertIn('--retry-failed-stage', flags, state['stop_reason'])
        self.assertNotIn('--grant-recovery', flags)
        run = str(self.run)
        self.launch(['--run-dir', run, '--no-chat', '--resolver-request', published['request_id'],
                     '--resolver-token', published['request_token'], '--resolver-response',
                     'provide_information', '--resolver-message', 'The provider quota was raised.'], 0)
        # Information is not authority: the advertised path never relies on a plain resume.
        held = self.launch(['--run-dir', run, '--resume-paused', '--no-chat'], 2)
        self.assertIn('retained the human guidance', held.stdout)
        _, before = self.saved()
        probe = self.root / 'retry-launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        result = self.launch(['--run-dir', run, '--resume-paused', '--retry-failed-stage', '--no-chat'], 2)
        self.assertNotIn('Input rejected', result.stderr)
        self.assertTrue(self.progress(before, probe), 'the advertised retry must move the run')
        launches = [json.loads(line)['stage'] for line in probe.read_text().splitlines()]
        self.assertEqual(before['next_stage'], launches[0], launches)
        self.assertEqual(1, launches.count(before['next_stage']), 'exactly one fresh attempt of the stopped stage')
        _, after = self.saved()
        [authorization] = after['failure_retry_authorizations']
        self.assertEqual('operational_exhaustion', authorization['kind'])
        self.assertEqual(before['recovery_context']['events'], authorization['events'])
        self.assertEqual(['failure_retry_authorized'],
                         [e['kind'] for e in after['user_events'] if e['kind'] == 'failure_retry_authorized'])
        for key in ('automatic_recoveries_since_resume', 'automatic_timeout_recoveries',
                    'automatic_permission_recoveries', 'failure_history'):
            self.assertEqual(before.get(key), after.get(key), f'{key} must not be reset')

    def test_three_pause_sequence_resumes_each_stop_with_its_advertised_command(self):
        """#301 as reported live: an external_directory denial, a user time cap, then attempt exhaustion."""
        denials = self.root / 'permission-denials'
        denials.write_text('2')
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_PERMISSION_DENIALS=str(denials))
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        self.run, held = self.saved()
        run = ['--run-dir', str(self.run), '--no-chat']
        # 1. The same denial twice holds the Builder; its one advertised retry launches one fresh attempt.
        flags, _ = self.advertised_commands(held)
        self.assertIn('--retry-failed-stage', flags, held.get('stop_reason'))
        self.assertNotIn('--grant-recovery', flags)
        self.launch([*run, '--resume-paused', '--retry-failed-stage', '--pause-after-stage'], 2)
        _, state = self.saved()
        builder = [row for row in state['stages'] if row.get('stage') == 'terra']
        self.assertEqual([True, True, False], [bool(row.get('rejected')) for row in builder])
        # 2. An explicit time cap: raising the bound continues in the same command.
        state['settings']['limits']['max_seconds'] = 7200
        state['settings'].setdefault('budget_origins', {})['max_seconds'] = 'user_explicit'
        state['active_seconds'] = 7300
        state = self.publish(state, 'PAUSED_TIME_LIMIT', 'Saved active-time limit reached at stage boundary')
        flags, _ = self.advertised_commands(state)
        self.assertIn('--max-seconds', flags)
        self.assertNotIn('--retry-failed-stage', flags)
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch([*run, '--resume-paused', '--max-seconds', '28800'], 2)
        _, state = self.saved()
        self.assertEqual('astra_review', state['active_stage']['stage'], 'the raised bound moved the run')
        self.launch([*run, '--abandon-stage', runner.attempt_id(state['active_stage'])], 0)
        # 3. AutoResolver's operational recoveries are exhausted at that stopped attempt.
        state = self.publish(self.saved()[1], 'PAUSED_RESOLVER_OPERATIONAL',
                             'AutoResolver exhausted its recorded operational recoveries')
        flags, _ = self.advertised_commands(state)
        self.assertIn('--retry-failed-stage', flags)
        self.assertNotIn('--grant-recovery', flags)
        self.launch([*run, '--resume-paused', '--retry-failed-stage'], 2)
        self.assertTrue(self.progress(state), 'the advertised retry must move the run')
        _, after = self.saved()
        self.assertEqual(['permission_hold', 'operational_exhaustion'],
                         [row['kind'] for row in after['failure_retry_authorizations']])
        self.assertEqual([1, 2], [row['repeat_count'] for row in after['automatic_permission_recoveries']])

    def describe_rejection(self, flag):
        probe = self.root / 'probe.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        args = ['--run-dir', str(self.run), '--no-chat']
        if flag == '--grant-recovery':
            args += ['--resume-paused', '--grant-recovery', '1']
        elif flag == '--resume-paused':
            args += ['--resume-paused']
        result = self.launch(args, 2)
        return result.stderr


class PermissionHoldConformanceTests(AdversarialCase):
    """A repeated external_directory denial holds until one explicit fresh attempt (#301)."""

    NOTE = 'Write scratch files only under the diagnostic_directory named in recovery_context.'

    def operator(self, *extra):
        """What an operator types at the saved run: one action, no launch or limit flags."""
        command = [*default_autocode(), '--workspace', str(self.project), '--run-dir', str(self.driver.run_dir),
                   '--no-chat', *extra]
        return run_cli(command, env=self.env, cwd=self.root, timeout=60)

    def advertised(self, view):
        need = view.get('needs') or {}
        texts = [str((view.get('recovery') or {}).get('saved_reason') or '')]
        for question in need.get('questions') or []:
            texts += [question.get('question') or '', *(question.get('options') or [])]
        return set(ADVERTISED_FLAGS.findall(' '.join(texts)))

    def held(self, fault):
        self.set_fault('recovery', fault)
        view = self.driver.drive(self.scenario.brief)
        self.assertEqual(2, len(self.trace('builder_permission_denied')), self.root)
        self.assertFalse(view['done'], view)
        self.assertEqual('operational_exhaustion', view['needs'].get('resolver_scope'), view)
        return view

    def test_permission_hold_advertises_one_fresh_attempt_and_it_resumes_the_run(self):
        view = self.held('builder_permission_until_retry')
        flags = self.advertised(view)
        self.assertIn('--retry-failed-stage', flags, view)
        self.assertNotIn('--grant-recovery', flags, 'a denial never spends the timeout-recovery budget')
        before = self.driver.state()  # evidence only
        need = view['needs']
        answered = self.operator('--resolver-request', need['resolver_request_id'], '--resolver-token',
                                 need['resolver_token'], '--resolver-response', 'provide_information',
                                 '--resolver-message', self.NOTE)
        self.assertEqual(0, answered.returncode, answered.stdout + answered.stderr)
        # Information is not authority: a plain resume still launches nothing.
        self.assertEqual(2, self.operator('--resume-paused').returncode)
        self.assertEqual(2, len(self.trace('builder_permission_denied')))
        self.assertFalse(self.trace('builder_permission_corrected'))
        retried = self.operator('--resume-paused', '--retry-failed-stage')
        self.assertNotIn('Input rejected', retried.stderr, retried.stdout + retried.stderr)
        self.assertEqual(1, len(self.trace('builder_permission_corrected')), self.root)
        handoff = self.trace('builder_permission_handoff')[-1]['recovery']
        self.assertEqual(self.NOTE, handoff['human_information']['text'], 'the information reaches the attempt')
        view = self.finish()
        self.assertEqual('TASK_COMPLETE', view['status'], view)
        state = self.driver.state()
        self.assertEqual(before['automatic_permission_recoveries'], state['automatic_permission_recoveries'])
        self.assertEqual(0, state.get('automatic_recoveries_since_resume', 0))
        granted = [e for e in state['user_events'] if e['kind'] == 'failure_retry_authorized']
        self.assertEqual(['user_cli'], [e.get('actor') for e in granted])
        self.assertEqual(['permission_hold'], [row['kind'] for row in state['failure_retry_authorizations']])

    def test_one_authorization_launches_one_attempt_and_a_repeat_holds_again(self):
        self.held('builder_permission_repeated')
        retried = self.operator('--resume-paused', '--retry-failed-stage')
        self.assertNotIn('Input rejected', retried.stderr, retried.stdout + retried.stderr)
        self.assertEqual(3, len(self.trace('builder_permission_denied')), self.root)
        for _ in range(2):
            self.assertEqual(2, self.operator('--resume-paused').returncode)
        self.assertEqual(3, len(self.trace('builder_permission_denied')), 'a used authorization launched again')
        state = self.driver.state()  # evidence only
        self.assertEqual([1, 2, 3], [row['repeat_count'] for row in state['automatic_permission_recoveries']])
        self.assertEqual(0, state.get('automatic_recoveries_since_resume', 0))
        self.assertFalse(self.trace('stage_enter', 'sol'))
        self.assertEqual(1, len(state['failure_retry_authorizations']))
        # Another attempt needs another explicit authorization. It buys at most one, and the
        # no-progress limit and recovery budget, which three Builder denials reach, still apply.
        self.assertIn('--retry-failed-stage', self.advertised(self.status()))
        self.assertNotIn('Input rejected', self.operator('--resume-paused', '--retry-failed-stage').stderr)
        self.assertLessEqual(len(self.trace('builder_permission_denied')), 4, self.root)
        self.assertFalse(self.status()['done'])


if __name__ == '__main__':
    unittest.main()
