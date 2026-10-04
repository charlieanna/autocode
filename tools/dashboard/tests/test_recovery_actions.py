"""Recovery buttons call existing CLI controls with fresh exact-pause identity."""
import copy
import unittest
from unittest.mock import patch
try:
    from . import test_registry_interventions as fixtures
except ImportError:
    import test_registry_interventions as fixtures


class RecoveryActionTests(unittest.TestCase):
    setUp = fixtures.RegistryInterventionTests.setUp
    save = fixtures.RegistryInterventionTests.save
    write_state = fixtures.RegistryInterventionTests.write_state
    console_new = fixtures.RegistryInterventionTests.console_new
    action = fixtures.RegistryInterventionTests.action

    def prepare(self, kind, **fields):
        action = {'id': kind, 'kind': kind, 'label': 'Reviewed recovery', 'effect': 'Keeps history', **fields}
        self.card = {'version': 1, 'status': 'PAUSED_REQUESTED', 'token': 'recovery-v1:exact', 'actions': [action]}
        self.status.update(status='PAUSED_REQUESTED', view={'recovery': self.card})
        self.save('status.json', self.status)
        return {'recovery_token': self.card['token'], 'recovery_action': kind}

    def check(self, kind, tail, **fields):
        payload = self.prepare(kind, **fields)
        before = (self.run / 'state.json').read_bytes()
        result = self.action('recover_pause', **payload)
        expected = ['--no-chat', '--expected-recovery-token', self.card['token'], *tail]
        self.assertEqual(expected, result['command'][-len(expected):])
        self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_resume_button_uses_existing_resume(self):
        self.check('resume', ['--resume-paused'])

    def test_abandon_button_does_not_resume(self):
        self.check('abandon', ['--abandon-stage', '004/builder-02'], attempt_id='004/builder-02')
        self.assertNotIn('--resume-paused', self.console.action_log(self.workspace, self.run)[0]['command'])

    def test_builder_button_names_only_current_members(self):
        self.check('retry_builder', ['--resume-paused', '--retry-builder', 'M1', '--retry-builder', 'M2'], milestone_ids=['M1','M2'])

    def test_job_button_passes_its_specific_saved_failure_token(self):
        self.check('retry_job', ['--resume-paused', '--retry-failed-stage', '--job-retry-token', 'job-exact'], job_retry_token='job-exact')

    def test_report_button_names_its_exact_attempt(self):
        self.check('retry_report', ['--resume-paused', '--retry-report', '007/validator-01'], attempt_id='007/validator-01')

    def test_repeated_failure_button_grants_an_explicit_attempt(self):
        self.check('retry_failed_stage', ['--resume-paused', '--retry-failed-stage'])

    def test_stale_token_action_removal_unavailable_status_and_nonexecution_actions_refuse(self):
        payload = self.prepare('resume')
        self.console.view(self.workspace, self.run)  # prime cached status
        self.card['token'] = 'new-token'
        self.save('status.json', self.status)
        with self.assertRaisesRegex(ValueError, 'pause changed'):
            self.action('recover_pause', **payload)
        payload['recovery_token'] = 'new-token'
        self.card['actions'] = []
        self.save('status.json', self.status)
        with self.assertRaisesRegex(ValueError, 'no longer offered'):
            self.action('recover_pause', **payload)
        payload = self.prepare('feedback')
        with self.assertRaisesRegex(ValueError, 'does not run'):
            self.action('recover_pause', **payload)
        with patch.object(self.console, '_json_command', return_value=(None, {'message':'status unavailable'})):
            with self.assertRaisesRegex(ValueError, 'status unavailable'):
                self.action('recover_pause', **payload)
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_busy_workspace_refuses_duplicate_recovery_and_client_cannot_supply_flags(self):
        payload = self.prepare('retry_builder', milestone_ids=['M1'])
        self.console.workspace_busy.add(str(self.workspace))
        with self.assertRaisesRegex(ValueError, 'already queued or running'):
            self.action('recover_pause', **payload)
        self.console.workspace_busy.clear()
        result = self.action('recover_pause', **payload, milestone_ids=['M999'], job_retry_token='injected', args=['--approve-goal','anything'])
        self.assertEqual(['--resume-paused','--retry-builder','M1'], result['command'][-3:])
        self.assertNotIn('M999', result['command'])
        self.assertNotIn('--approve-goal', result['command'])

    def test_stale_running_worker_gets_inspection_but_no_execution_token(self):
        from dashboard_recovery import projection
        for check in (False, True):
            with self.subTest(runner_check=check):
                status={'status':'RUNNING','stale':True,'view':{'recovery':None,'runner_check':{'stage':'regression_proof'} if check else None},
                        'active_stage':None if check else {'stage':'terra'},
                        'runner_check_workers':{'checked':True,'alive':False} if check else None}
                card=projection(status)
                self.assertIsNone(card['token'])
                self.assertEqual('RUNNING',card['status'])
                self.assertEqual(['inspect','feedback'],[row['kind'] for row in card['actions']])
                for field in ('what_happened','retained','actions'):
                    self.assertTrue(card[field])
                self.status.update(status)
                self.save('status.json',self.status)
                with self.assertRaisesRegex(ValueError,'pause changed'):
                    self.action('recover_pause',recovery_token=None,recovery_action='resume')
        self.assertIsNone(projection({'status':'RUNNING','stale':False,'view':{'recovery':None}}))
        self.assertEqual([],self.console.action_log(self.workspace,self.run))

    def test_projected_card_is_from_supported_status_and_absent_on_inspection_failure(self):
        self.prepare('resume')
        self.state['recovery'] = {'token':'private-stale'}
        self.write_state()
        view = self.console.view(self.workspace, self.run)
        self.assertEqual(self.card, view['interventions']['recovery'])
        self.console.status_cache.clear()
        with patch.object(self.console, '_json_command', return_value=(None, {'message':'offline','uncertain':False})):
            self.assertIsNone(self.console.view(self.workspace,self.run)['interventions']['recovery'])
