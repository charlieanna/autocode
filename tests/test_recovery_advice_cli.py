"""Every command an operational stop advertises works at that stop (#288, #301).

Each test runs the real CLI against the offline fake provider. A stop's advice is
read from its published AutoResolver request and executed exactly as written; a
provider launch recorded by the fixture proves the run continued.
"""
import copy
import re
import shlex
import subprocess
import unittest

import autocode as runner, autocode_resolver_human as human
from . import test_human_publication, test_subprocess

ADVICE = re.compile(r'--resume-paused (--[a-z-]+) N')
REFRESH = re.compile(r'Run `(autocode [^`]+)`')
PERMISSION_CAUSE = ("The prior request was stopped by OpenCode's external_directory permission. "
                    'Use only workspace-contained evidence paths; do not use /tmp, default mktemp paths, '
                    'nohup, or detached processes.')


class RecoveryAdviceCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')
    timeout_exhausted_checkpoint = test_human_publication.HumanResponseCLITests.timeout_exhausted_checkpoint
    stopped_operational_checkpoint = test_human_publication.HumanResponseCLITests.stopped_operational_checkpoint

    def launches(self):
        probe = self.root / 'launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        return len(probe.read_text().splitlines()) if probe.exists() else 0

    def published(self):
        run, state = self.saved()
        public = human.current(state)
        self.assertIsNotNone(public, state.get('stop_reason'))
        self.assertEqual('operational_exhaustion', public['scope'])
        origin = state['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']
        return public, origin

    def advertised(self, public):
        found = ADVICE.findall(public['request']['decision_needed'])
        self.assertEqual(1, len(found), public['request']['decision_needed'])
        return found[0]

    def run_advertised(self, flag, value):
        run, _ = self.saved()
        return self.launch(['--run-dir', str(run), '--resume-paused', flag, str(value), '--no-chat'], 2)

    def run_refresh(self, text):
        match = REFRESH.search(text)
        self.assertIsNotNone(match, text)
        words = shlex.split(match.group(1))[1:]
        result = subprocess.run([*self.entry, *words], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)

    def settle(self, **changes):
        """Set aside the stopped attempt and its request, then saved accounting for the next stop."""
        run, state = self.saved()
        if state.get('active_stage'):
            self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(state['active_stage'])], 0)
            run, state = self.saved()
        human.supersede_operational(state, 'Fixture prepared the next operational stop')
        state.update(status='PAUSED_STAGE_ABANDONED', stop_reason='Fixture boundary', **changes)
        runner.write_json(run / 'state.json', state)

    def test_grant_after_fixing_the_cause_in_the_workspace_resumes(self):
        run, exhausted = self.timeout_exhausted_checkpoint()
        public, origin = self.published()
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', origin['pause_status'])
        self.assertEqual('--grant-recovery', self.advertised(public))
        (self.project / 'greet.py').write_text('# the operator fixed the cause\n')
        before = self.launches()
        rejected = self.run_advertised('--grant-recovery', 1)
        self.assertIn('out of date', rejected.stderr)
        self.assertEqual(before, self.launches())
        self.assertFalse(self.saved()[1].get('recovery_grants'))
        self.run_refresh(rejected.stderr)
        self.assertEqual(before, self.launches(), 'publishing a current request launches nothing')
        fresh, origin = self.published()
        self.assertNotEqual(public['request_id'], fresh['request_id'])
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', origin['pause_status'])
        refreshed = self.saved()[1]
        for key in ('automatic_recoveries_since_resume', 'automatic_timeout_recoveries', 'settings', 'goal_contract'):
            self.assertEqual(exhausted.get(key), refreshed.get(key), key)
        result = self.run_advertised(self.advertised(fresh), 1)
        self.assertIn('Recovery grant recorded: 1', result.stdout)
        self.assertGreater(self.launches(), before)
        granted = self.saved()[1]
        self.assertEqual(exhausted['automatic_timeout_recoveries'], granted['automatic_timeout_recoveries'])
        self.assertEqual([fresh['request_id']], [row['request_id'] for row in granted['recovery_grants']])
        self.assertEqual(2, granted['recovery_grants'][0]['remaining_count'])

    def test_changing_stage_limits_without_a_grant_keeps_the_grant_working(self):
        run, _ = self.timeout_exhausted_checkpoint()
        before = self.launches()
        self.launch(['--run-dir', str(run), '--resume-paused', '--max-idle-seconds', '900', '--no-chat'], 2)
        self.assertEqual(before, self.launches())
        public, origin = self.published()
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', origin['pause_status'])
        self.assertEqual(900, self.saved()[1]['settings']['limits']['idle_timeout_seconds'])
        result = self.run_advertised(self.advertised(public), 1)
        self.assertIn('Recovery grant recorded: 1', result.stdout)
        self.assertGreater(self.launches(), before)

    def test_explicit_time_limit_after_an_answer_continues_without_another_request(self):
        run, _ = self.stopped_operational_checkpoint()
        public, origin = self.published()
        self.assertEqual('PAUSED_TIME_LIMIT', origin['pause_status'])
        flag = self.advertised(public)
        self.assertEqual('--max-seconds', flag)
        self.launch(['--run-dir', str(run), '--resolver-request', public['request_id'],
                     '--resolver-response', 'provide_information', '--resolver-message', 'More time is approved',
                     '--resolver-token', public['request_token']], 0)
        answered = self.saved()[1]
        before = self.launches()
        self.run_advertised(flag, 50000)
        self.assertGreater(self.launches(), before)
        resumed = self.saved()[1]
        self.assertEqual(50000, resumed['settings']['limits']['max_seconds'])
        self.assertFalse(any(entry['identity']['proposal']['origin'].get('pause_status') == 'PAUSED_TIME_LIMIT'
                             for key, entry in resumed['resolver']['human_escalations'].items()
                             if key not in answered['resolver']['human_escalations']))

    def test_iteration_limit_advice_continues_in_the_advertised_command(self):
        run, start = self.stopped_operational_checkpoint()
        start['settings']['limits']['iteration_ceiling'] = start['iteration'] - 1
        start['settings'].setdefault('budget_origins', {})['iteration_ceiling'] = 'user_explicit'
        self.settle(settings=start['settings'])
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        public, origin = self.published()
        self.assertEqual(('PAUSED_ITERATION_LIMIT', 'explicit_user_cap'), (origin['pause_status'], origin['budget']['category']))
        before = self.launches()
        self.run_advertised(self.advertised(public), start['iteration'] + 1)
        self.assertGreater(self.launches(), before)

    def test_external_directory_then_time_limit_then_recovery_exhaustion(self):
        run, start = self.stopped_operational_checkpoint()
        permission = {'stage': 'terra', 'role': 'terra', 'next_stage': start['next_stage'],
                      'instruction': PERMISSION_CAUSE, 'repeat_count': 1,
                      'denied_operation': {'capability': 'external_directory', 'path': '/tmp/out.txt',
                                           'classification': 'external_temporary_directory'}}
        self.settle(recovery_context=copy.deepcopy(permission), automatic_recoveries_since_resume=3,
                    automatic_permission_recoveries=[copy.deepcopy(permission) for _ in range(3)])

        # 1. Exhausted workspace-permission recoveries; the operator fixes the cause.
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        public, origin = self.published()
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', origin['pause_status'])
        self.assertIn('external_directory', self.saved()[1]['stop_reason'])
        (self.project / 'evidence-path.txt').write_text('workspace-only evidence\n')
        before = self.launches()
        rejected = self.run_advertised(self.advertised(public), 1)
        self.run_refresh(rejected.stderr)
        self.assertIn('Recovery grant recorded', self.run_advertised(self.advertised(self.published()[0]), 1).stdout)
        self.assertGreater(self.launches(), before)

        # 2. The operator's explicit active-time cap is reached; they answer, then raise it.
        self.settle(active_seconds=7300)
        self.launch(['--run-dir', str(run), '--resume-paused', '--max-seconds', '7200', '--no-chat'], 2)
        public, origin = self.published()
        self.assertEqual(('PAUSED_TIME_LIMIT', 'explicit_user_cap'), (origin['pause_status'], origin['budget']['category']))
        self.launch(['--run-dir', str(run), '--resolver-request', public['request_id'],
                     '--resolver-response', 'provide_information', '--resolver-message', 'Raise the cap',
                     '--resolver-token', public['request_token']], 0)
        before = self.launches()
        self.run_advertised(self.advertised(public), 28800)
        self.assertGreater(self.launches(), before)

        # 3. The automatic recovery allowance is exhausted again.
        self.settle(automatic_recoveries_since_resume=3)
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat'], 2)
        public, origin = self.published()
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', origin['pause_status'])
        before = self.launches()
        self.assertIn('Recovery grant recorded', self.run_advertised(self.advertised(public), 1).stdout)
        self.assertGreater(self.launches(), before)
        final = self.saved()[1]
        self.assertEqual(2, len(final['recovery_grants']))
        self.assertEqual(3, len(final['automatic_permission_recoveries']))


if __name__ == '__main__':
    unittest.main()
