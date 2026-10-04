"""A planning timeout cannot exhaust a Builder's unchanged-batch allowance."""
import copy
import unittest

import autocode_recovery_progress as progress


class RecoveryProgressTests(unittest.TestCase):
    def state(self):
        return {'status': 'WAITING_FOR_USER', 'no_progress_batches': 3,
                'automatic_recoveries_since_resume': 2, 'consecutive_timeout_recoveries': 0,
                'automatic_timeout_recoveries': [
                    {'stage': 'requirements_gather', 'attempt_id': str(i), 'changed_files': []}
                    for i in range(3)],
                'stages': [{'stage': 'requirements_gather', 'automatic_recovery': True},
                           {'stage': 'resolver', 'runner_owned': True},
                           {'stage': 'astra_finalize'}],
                'resolver': {'human_escalations': {'request': {'identity': {'proposal': {
                    'origin': {'pause_status': 'PAUSED_NO_PROGRESS'}}}}}}}

    def reconcile(self, state, *, issued=True, approved=True, supersede=True):
        return progress.reconcile(state,
            issued={'request_id': 'request', 'scope': 'operational_exhaustion'} if issued else None,
            approved=approved, supersede=lambda *_: supersede, now=lambda: 'now')

    def test_correcting_prebuild_hold_preserves_spent_timeout_allowance_and_history(self):
        state = self.state()
        before = copy.deepcopy(state)
        self.assertTrue(self.reconcile(state))
        self.assertEqual(('RUNNING', 0), (state['status'], state['no_progress_batches']))
        for key in ('automatic_recoveries_since_resume', 'consecutive_timeout_recoveries',
                    'automatic_timeout_recoveries', 'stages'):
            self.assertEqual(before[key], state[key])
        self.assertEqual(3, state['user_events'][-1]['previous_no_progress_batches'])
        self.assertFalse(self.reconcile(state), 'the correction must apply only once')

    def test_stale_unapproved_or_unretirable_request_cannot_resume(self):
        for option in ('issued', 'approved', 'supersede'):
            with self.subTest(option=option):
                state = self.state(); before = copy.deepcopy(state)
                self.assertFalse(self.reconcile(state, **{option: False}))
                self.assertEqual(before, state)

    def test_implementation_or_parallel_worker_history_keeps_its_counter(self):
        for mutation in (
                lambda s: s['stages'].append({'stage': 'terra'}),
                lambda s: s.update(orchestration_history=[{'workers': [{'status': 'FAILED'}]}]),
                lambda s: s.update(orchestration_batch={'status': 'BUILDING'}),
                lambda s: s.update(implementation={'summary': 'candidate'}),
                lambda s: s['stages'].append({'stage': 'unknown_future_stage'})):
            state = self.state(); mutation(state); before = copy.deepcopy(state)
            self.assertFalse(self.reconcile(state))
            self.assertEqual(before, state)

    def test_unattributed_or_duplicate_recoveries_cannot_clear_a_counter(self):
        state = self.state()
        state['automatic_timeout_recoveries'] = [state['automatic_timeout_recoveries'][0]] * 3
        self.assertFalse(self.reconcile(state))
        state = self.state(); state['no_progress_batches'] = 4
        self.assertFalse(self.reconcile(state))

    def test_other_pause_kinds_retain_their_boundary(self):
        state = self.state()
        state['resolver']['human_escalations']['request']['identity']['proposal']['origin'][
            'pause_status'] = 'PAUSED_TIMEOUT_RECOVERY'
        before = copy.deepcopy(state)
        self.assertFalse(self.reconcile(state))
        self.assertEqual(before, state)
