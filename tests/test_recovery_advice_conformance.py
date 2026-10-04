"""Every recovery action a stop advertises must be accepted there, and one
documented action must move the run again (#288/#301).

Real CLI processes with an explicitly fake provider; no sleeps, no live models.
Checkpoints mirror the pause classes reported live: exhausted automatic
timeout recovery, an explicit user time cap, and exhausted AutoResolver
operational-recovery attempts.
"""
import json
import re
from pathlib import Path
import unittest

from . import test_subprocess
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
        self.assertNotIn('--retry-failed-stage', flags)
        for flag in sorted(flags):
            if flag in ('--resolver-response',):
                continue  # answered through the request's own options, not a bare CLI retry
            with self.subTest(flag=flag):
                self.assertNotIn('requires', self.describe_rejection(flag))

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


if __name__ == '__main__':
    unittest.main()
