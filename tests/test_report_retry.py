"""Bounded report recovery policy and supplemental public CLI regressions.

The fixture transport is offline; native fault-injection evidence is recorded
separately. These checks guard the observed recovery routes and retained bounds.
"""
import copy
import shutil
import unittest
from pathlib import Path

import autocode_failures as failures
import autocode_report_retry as retry
import autocode_run_view as run_view
from autocode_taskrun import TaskRun, TaskRunError

from . import test_subprocess
from .opencode_fixture_cli import entrypoint


class ReportRetryPolicyTests(unittest.TestCase):
    def state(self):
        identity = {'stage': 'sol', 'artifact_hash': 'source', 'error_class': 'RuntimeError'}
        key = failures.key(identity)
        return {'status': 'PAUSED_REPEATED_FAILURE', 'next_stage': 'sol',
                'settings': {'report_repair': {'max_attempts': 2}},
                'pending_report_repair': {'attempts': 1, 'error': retry.RETRYABLE_ERRORS[0],
                    'original': {'stage': 'sol', 'source_revision': 'source', 'failure_key': key},
                    'latest_rejected': {'failure_key': key, 'iteration': 1, 'output': '/run/repair-01.json'}},
                'failure_history': {key: {'identity': identity, 'count': 3, 'streak': 3}}}

    def test_partial_limit_requires_this_exact_consecutive_failure(self):
        state = self.state()
        self.assertTrue(retry.bounded_failure(state, 2))
        for case in ('not_paused', 'not_repeated', 'other_repair', 'other_identity', 'missing_ledger', 'negative', 'over_limit', 'boolean'):
            with self.subTest(case=case):
                changed = copy.deepcopy(state)
                pending = changed['pending_report_repair']
                entry = next(iter(changed['failure_history'].values()))
                if case == 'not_paused': changed['status'] = 'RUNNING'
                elif case == 'not_repeated': entry['streak'] = 2
                elif case == 'other_repair': pending['latest_rejected']['failure_key'] = 'other'
                elif case == 'other_identity': entry['identity']['artifact_hash'] = 'other'
                elif case == 'missing_ledger': changed['failure_history'] = {}
                else: pending['attempts'] = {'negative': -1, 'over_limit': 3, 'boolean': True}[case]
                self.assertFalse(retry.bounded_failure(changed, 2))
        state['pending_report_repair']['attempts'] = 2
        state['failure_history'] = {}
        self.assertTrue(retry.bounded_failure(state, 2))
        self.assertFalse(retry.bounded_failure(state, 0))

    def test_status_distinguishes_exact_retry_changed_source_and_active_work(self):
        state = self.state()
        for error in retry.RETRYABLE_ERRORS:
            state['pending_report_repair']['error'] = error
            self.assertEqual('--resume-paused --retry-report 001/repair-01', run_view.needs(state)['action'])
        changed = run_view.needs(state, stale_report_repair=True)
        self.assertEqual('--resume-paused', changed['action'])
        self.assertNotIn('retry_report_attempt', changed)
        for key in ('active_stage', 'active_runner_check', 'uncertain_artifacts'):
            with self.subTest(key=key):
                held = {**state, key: {'stage': 'sol'}}
                self.assertNotIn('retry_report_attempt', run_view.needs(held))
                actions = [row['kind'] for row in run_view.view(held)['recovery']['actions']]
                self.assertNotIn('retry_failed_stage', actions)
        for stage in ('terra', 'astra_review'):
            held = self.state()
            held['next_stage'] = stage
            identity = {'stage': stage, 'artifact_hash': 'source', 'error_class': 'RuntimeError'}
            key = failures.key(identity)
            held['pending_report_repair']['original'].update(stage=stage, failure_key=key)
            held['pending_report_repair']['latest_rejected']['failure_key'] = key
            held['failure_history'] = {key: {'identity': identity, 'count': 3, 'streak': 3}}
            for used in (1, 2):
                with self.subTest(stage=stage, repairs=used):
                    held['pending_report_repair']['attempts'] = used
                    projected = run_view.view(held)
                    self.assertNotIn('retry_report_attempt', projected['needs'])
                    actions = [row['kind'] for row in projected['recovery']['actions']]
                    self.assertNotIn('retry_report', actions)
                    self.assertNotIn('retry_failed_stage', actions)

    def test_fresh_attempt_stopped_before_any_repair_names_itself(self):
        # An explicit retry that fails the same way is stopped by the bound before a correction or repair runs.
        state = self.state()
        key = state['pending_report_repair']['original']['failure_key']
        fresh = {'stage': 'sol', 'iteration': 2, 'output': '/run/validator-02.json', 'rejected': True,
                 'source_revision': 'source', 'failure_key': key, 'failure_attempt': '2:sol:/run/validator-02.json'}
        state['pending_report_repair'] = {'attempts': 0, 'error': retry.RETRYABLE_ERRORS[0], 'original': fresh}
        state['failure_history'][key].update(count=4, streak=4,
                                             attempts=['1:sol:/run/validator-01.json', fresh['failure_attempt']])
        self.assertTrue(retry.bounded_failure(state, 2))
        self.assertEqual('--resume-paused --retry-report 002/validator-02', run_view.needs(state)['action'])
        for case in ('corrected', 'repair_used', 'later_failure', 'not_rejected'):
            with self.subTest(case=case):
                changed = copy.deepcopy(state)
                pending = changed['pending_report_repair']
                if case == 'corrected': pending['correction_attempted'] = True
                elif case == 'repair_used': pending['attempts'] = 1
                elif case == 'later_failure': changed['failure_history'][key]['attempts'].append('3:sol:/run/validator-03.json')
                else: pending['original'].pop('rejected')
                self.assertFalse(retry.bounded_failure(changed, 2))
                self.assertNotIn('retry_report_attempt', run_view.needs(changed))


class ReportRetryCLITests(unittest.TestCase):
    new_run_engine_args = ('--engine', 'opencode')
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / 'tools/fake_opencode.py'
        provider = self.root / 'fixture-bin/opencode'
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        self.entry = entrypoint(self.entry)
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_REPORT_LOSS'] = str(self.root / 'complete-report.json')
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(self.root / 'launches.jsonl')

    def stopped(self):
        self.launch(['Build a greeting tool', '--chat'], 2, answers='CLI\nyes\n')
        run, before = self.saved()
        pending = before['pending_report_repair']
        self.assertEqual(1, pending['attempts'])
        self.assertTrue(pending['correction_attempted'])
        return TaskRun(self.project, run, command=self.entry, env=self.env, timeout=60), before

    def paused(self):
        public, before = self.stopped()
        self.env.pop('AUTOCODE_FIXTURE_REPORT_LOSS')
        need = public.resume_paused()['needs']
        self.assertEqual('operational_exhaustion', need['resolver_scope'])
        return public, need, before

    def respond(self, public, need):
        public.respond_operational(need['resolver_request_id'], need['resolver_token'],
                                   'Report delivery has been restored; retain existing limits and evidence.')

    def assert_preserved(self, before):
        after = self.saved()[1]
        self.assertEqual(before['settings']['limits'], after['settings']['limits'])
        key = before['pending_report_repair']['original']['failure_key']
        self.assertEqual(before['failure_history'][key]['count'], after['failure_history'][key]['count'])
        self.assertEqual(before['failure_history'][key]['attempts'], after['failure_history'][key]['attempts'])
        self.assertEqual(1, after['report_repair_archive'][-1]['repair']['attempts'])
        self.assertEqual(2, sum(row['stage'] == 'sol' for row in after['stages']))

    def test_exact_report_retry_runs_fresh_validation_after_partial_repair_stop(self):
        public, need, before = self.paused()
        self.respond(public, need)
        held = public.resume_paused()
        self.assertEqual('PAUSED_REPEATED_FAILURE', held['status'])
        attempt = held['needs']['retry_report_attempt']
        calls = (self.root / 'launches.jsonl').read_bytes()
        with self.assertRaises(TaskRunError):
            public.retry_report(attempt + '-wrong')
        self.assertEqual(calls, (self.root / 'launches.jsonl').read_bytes())
        events = Path(before['pending_report_repair']['original']['events'])
        contents, mode = events.read_bytes(), events.stat().st_mode
        # Deliberately corrupt an owned disposable archive, past its read-only mode.
        events.chmod(mode | 0o200)
        try:
            events.write_bytes(contents + b'{}\n')
            with self.assertRaisesRegex(TaskRunError, 'Saved report inputs changed'):
                public.retry_report(attempt)
            self.assertEqual(calls, (self.root / 'launches.jsonl').read_bytes())
            self.assertEqual(before['pending_report_repair'], self.saved()[1]['pending_report_repair'])
        finally:
            events.write_bytes(contents)
            events.chmod(mode)
        self.assertTrue(public.retry_report(attempt)['done'])
        self.assert_preserved(before)

    def test_investigator_report_repair_preserves_the_testers_retry(self):
        self.env['AUTOCODE_FIXTURE_INVALID_INVESTIGATOR'] = '1'
        public, need, before = self.paused()
        accepted = before.get('report_repair_history', [])
        self.assertTrue(any(row.get('result') == 'accepted'
                            and row['repair']['original_stage'] == 'investigate_stuck'
                            for row in accepted))
        self.respond(public, need)
        held = public.resume_paused()
        self.assertTrue(public.retry_report(held['needs']['retry_report_attempt'])['done'])
        self.assert_preserved(before)

    def test_answered_request_does_not_hold_a_source_only_stale_repair(self):
        public, need, before = self.paused()
        path = self.project / 'greet.py'
        path.write_text(path.read_text() + '\n# Operator corrected the report delivery.\n')
        self.respond(public, need)  # The displayed request is re-bound after the source edit.
        view = public.status()
        self.assertEqual('--resume-paused', view['needs']['action'])
        self.assertIn('resume', [row['kind'] for row in view['recovery']['actions']])
        self.assertNotIn('retry_report_attempt', view['needs'])
        self.assertTrue(public.resume_paused()['done'])
        self.assert_preserved(before)

    def test_retry_that_fails_the_same_way_names_its_own_fresh_attempt(self):
        public, before = self.stopped()
        first = public.status()['needs']['retry_report_attempt']
        key = before['pending_report_repair']['original']['failure_key']
        stages = lambda: [row['stage'] for row in self.saved()[1]['stages']]
        # Delivery is still broken: one fresh Validator attempt, stopped by the bound before any repair.
        need = public.retry_report(first)['needs']
        self.assertEqual(['sol'], [stage for stage in stages()[len(before['stages']):]
                                   if stage.startswith('sol')])
        self.assertEqual(4, self.saved()[1]['failure_history'][key]['streak'])
        self.assertEqual('operational_exhaustion', need['resolver_scope'])
        self.respond(public, need)
        state = self.saved()[1]
        fresh = state['pending_report_repair']['original']
        attempt = f"{fresh['iteration']:03d}/{Path(fresh['output']).stem}"
        self.assertEqual(attempt, public.status()['needs']['retry_report_attempt'])
        self.assertNotEqual(first, attempt)
        calls = (self.root / 'launches.jsonl').read_bytes()
        with self.assertRaises(TaskRunError):
            public.retry_report(first)  # never an earlier, archived repair
        self.assertEqual(calls, (self.root / 'launches.jsonl').read_bytes())
        self.env.pop('AUTOCODE_FIXTURE_REPORT_LOSS')
        self.assertTrue(public.retry_report(attempt)['done'])
        after = self.saved()[1]
        self.assertEqual(4, after['failure_history'][key]['count'])
        self.assertEqual(before['settings']['limits'], after['settings']['limits'])
        self.assertEqual([1, 0], [row['repair']['attempts'] for row in after['report_repair_archive'][-2:]])
        self.assertEqual([first, attempt], [event['attempt_id'] for event in after['user_events']
                                            if event['kind'] == 'report_retry_after_format_fix'])

    def test_report_retry_after_a_source_edit_names_the_resume_that_validates_it(self):
        public, before = self.stopped()
        attempt = public.status()['needs']['retry_report_attempt']
        self.env.pop('AUTOCODE_FIXTURE_REPORT_LOSS')
        path = self.project / 'greet.py'
        path.write_text(path.read_text() + '\n# Operator corrected the report delivery.\n')
        calls = (self.root / 'launches.jsonl').read_bytes()
        with self.assertRaisesRegex(TaskRunError, 'use --resume-paused to archive the stale repair'):
            public.retry_report(attempt)
        self.assertEqual(calls, (self.root / 'launches.jsonl').read_bytes())
        self.assertEqual(before['pending_report_repair'], self.saved()[1]['pending_report_repair'])
        # No AutoResolver request is published yet, so the explicit resume archives the stale repair at once.
        self.assertTrue(public.resume_paused()['done'])
        self.assert_preserved(before)
        self.assertEqual('Source changed while paused; the repair no longer applies',
                         self.saved()[1]['report_repair_archive'][-1]['reason'])



if __name__ == '__main__':
    unittest.main()
