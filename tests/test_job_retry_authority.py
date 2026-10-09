"""Historical workflow-job records are not retry authority for a later pause.

Model-free controller tests: real authorization, captured persistence, and a
pre-dispatch sentinel. No provider, saved checkpoint or accepted report fixture.
"""
import contextlib
import copy
import io
import unittest
from unittest.mock import Mock, patch

import autocode as runner
import autocode_args
import autocode_job_failure as job_failure
import autocode_job_source as job_source
import autocode_run_actions as actions
import autocode_stage_recovery as stage_recovery
from .test_stale_job_failure import FAILURE
from .test_verify import Project


class BeforeDispatch(Exception):
    pass


class JobRetryAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({'README.md': 'unchanged source\n'})
        self.addCleanup(self.project.temp.cleanup)
        self.root = self.project.root
        self.run = self.root / '.autocode/runs/authority'
        self.state = {'task_id': 'authority', 'task': 'Preserve the source',
                      'workspace': str(self.root), 'run_dir': str(self.run),
                      'status': 'PAUSED_RATE_LIMIT', 'next_stage': 'sol',
                      'stop_reason': 'The current provider is rate limited',
                      'settings': {}, 'stages': [], 'history': [],
                      'answers': {}, 'user_events': [], 'iteration': 1,
                      'active_seconds': 17, 'recovery_context': {},
                      'job_failure': copy.deepcopy(FAILURE)}

    def invoke(self, token=None):
        parser = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        argv = ['--resume-paused', '--retry-failed-stage', '--no-chat']
        if token is not None:
            argv += ['--job-retry-token', token]
        args = parser.parse_args(argv)
        services = Mock(wraps=runner)
        services.ReportRepairQueued = runner.ReportRepairQueued
        services.prepare_abandoned_completion_revalidation.side_effect = BeforeDispatch
        services.run_role.side_effect = AssertionError('No provider is permitted in this test')
        writes = []
        services.write_json.side_effect = lambda path, state: writes.append(copy.deepcopy(state))
        with patch.object(runner.resolver_recovery, 'authorize_retry', wraps=runner.resolver_recovery.authorize_retry) as operational, \
                patch.object(runner, 'authorize_failure_retry', wraps=runner.authorize_failure_retry) as ordinary, \
                patch.object(stage_recovery.records, 'write_json', side_effect=lambda path, state: writes.append(copy.deepcopy(state))), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                result = actions.handle(services, args, parser, self.state, self.run / 'state.json', self.run, self.root)
            except BeforeDispatch:
                result = 'admitted'
        services.run_role.assert_not_called()
        services.consume_interventions.assert_not_called()
        return result, args, services, operational.call_count, ordinary.call_count, writes

    def test_historical_job_record_cannot_skip_unscoped_retry_validation(self):
        failure = copy.deepcopy(self.state['job_failure'])
        result, _, services, operational, ordinary, writes = self.invoke()
        self.assertEqual(2, result, 'Unscoped retry must reject before resumed preparation')
        self.assertEqual((1, 1), (operational, ordinary))
        services.prepare_abandoned_completion_revalidation.assert_not_called()
        self.assertEqual([], writes)
        self.assertEqual(failure, self.state['job_failure'])
        self.assertEqual('PAUSED_RATE_LIMIT', self.state['status'])

    def test_ordinary_repeated_failure_retry_with_and_without_historical_job(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                self.state.update(status='PAUSED_REPEATED_FAILURE', next_stage='terra', stages=[], failure_history={})
                if historical:
                    self.state['job_failure'] = copy.deepcopy(FAILURE)
                else:
                    self.state.pop('job_failure', None)
                revision = runner.support.snapshot(self.root)['revision']
                for stage, count in (('sol', 1), ('terra', 3)):
                    for index in range(count):
                        row = {'stage': stage, 'iteration': 1, 'role': stage,
                               'output': str(self.run / f'{stage}-{index}.json'),
                               'source_revision': revision, 'rejected': True}
                        runner.failures.record(self.state, row, ValueError('invalid report'), runner.now())
                        self.state['stages'].append(row)
                history, stages = copy.deepcopy(self.state['failure_history']), copy.deepcopy(self.state['stages'])
                result, args, _, operational, ordinary, writes = self.invoke()
                self.assertEqual('admitted', result)
                self.assertEqual((1, 1), (operational, ordinary))
                self.assertTrue(args._failure_retry_authorization)
                self.assertEqual(history, self.state['failure_history'])
                self.assertEqual(stages, self.state['stages'])
                self.assertTrue(writes, 'A valid ordinary retry records its exact authorization')

    def test_exact_job_retry_still_uses_the_matching_job_token(self):
        self.state.update(status='PAUSED_JOB_FAILURE', next_stage='investigate_stuck',
                          recovery_context={'kind': 'job_failure', 'stage': 'investigate_stuck',
                                            'attempt_id': FAILURE['attempt_id']})
        self.state['job_failure'].update(source_identity=runner.support.digest(job_source.identity(self.root)),
                                         configuration=job_failure.configuration(self.state))
        before = copy.deepcopy(self.state['job_failure'])
        result, _, _, operational, ordinary, writes = self.invoke('jr:investigator')
        self.assertEqual('admitted', result)
        self.assertEqual((0, 0), (operational, ordinary))
        self.assertEqual(before, self.state['job_failure'])
        self.assertEqual('jr:investigator', self.state['job_retry_authorization']['token'])
        self.assertTrue(writes)

    def test_missing_or_wrong_job_token_rejects_before_dispatch(self):
        self.state.update(status='PAUSED_JOB_FAILURE', next_stage='investigate_stuck',
                          recovery_context={'kind': 'job_failure', 'stage': 'investigate_stuck',
                                            'attempt_id': FAILURE['attempt_id']})
        self.state['job_failure'].update(source_identity=runner.support.digest(job_source.identity(self.root)),
                                         configuration=job_failure.configuration(self.state))
        before = copy.deepcopy(self.state['job_failure'])
        for token in (None, 'jr:wrong'):
            with self.subTest(token=token):
                result, _, services, operational, ordinary, writes = self.invoke(token)
                self.assertEqual(2, result)
                self.assertEqual((0, 0), (operational, ordinary))
                self.assertEqual([], writes)
                services.prepare_abandoned_completion_revalidation.assert_not_called()
                self.assertEqual(before, self.state['job_failure'])
