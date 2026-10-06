"""Pure liveness classification and additive public status projection."""
from __future__ import annotations

import copy
import unittest

import autocode_liveness as liveness
import autocode_run_view as run_view


def metadata():
    return {'schema': 1, 'nonce': 'a' * 32, 'receipt': '/run/attempt-supervision.json',
            'owner': {'pid': 101, 'birth_identity': 123.25},
            'keeper': {'pid': 102, 'birth_identity': 124.25},
            'provider': {'pid': 103, 'birth_identity': 125.25},
            'started_at': '2026-10-05T20:00:00Z'}


def inspection(saved, *, owner=True, keeper=True, provider=True, phase='armed', cause=None, error=None):
    receipt = {**copy.deepcopy(saved), 'phase': phase, 'cause': cause, 'cleanup_error': error,
               'observed_at': '2026-10-05T20:00:01Z', 'processes': [copy.deepcopy(saved['provider'])]}
    return {'owner': {'checked': True, 'alive': owner},
            'keeper': {'checked': True, 'alive': keeper},
            'provider': {'checked': True, 'alive': provider}, 'receipt': receipt}


class LivenessTests(unittest.TestCase):
    def test_missing_or_legacy_metadata_is_unknown_even_if_processes_are_reported_alive(self):
        saved = metadata(); current = inspection(saved)
        for missing in (None, {}, {'provider_pid': 103}, {'nonce': 'a' * 32}):
            with self.subTest(metadata=missing):
                result = liveness.classify(missing, current)
                self.assertEqual('unknown', result['kind'])
                self.assertTrue(all(result[role] == {'checked': False, 'alive': None} for role in liveness.ROLES))

    def test_missing_and_denied_inspection_are_unknown_never_dead(self):
        saved = metadata()
        for missing in (None, {}, [], False):
            with self.subTest(inspection=missing):
                result = liveness.classify(saved, missing)
                self.assertEqual('unknown', result['kind'])
                self.assertTrue(all(result[role]['alive'] is None for role in liveness.ROLES))
        for role in liveness.ROLES:
            for denied in (None, {}, {'checked': False, 'alive': False},
                           {'checked': False, 'alive': True}, {'checked': True, 'alive': None},
                           {'checked': 1, 'alive': False}, {'checked': True, 'alive': 0}):
                with self.subTest(role=role, observation=denied):
                    current = inspection(saved); current[role] = denied
                    result = liveness.classify(saved, current)
                    self.assertEqual('unknown', result['kind'])
                    self.assertEqual({'checked': False, 'alive': None}, result[role])

    def test_only_fresh_live_owner_keeper_provider_and_bound_armed_receipt_are_supervised(self):
        saved = metadata(); current = inspection(saved)
        self.assertEqual('supervised', liveness.classify(saved, current)['kind'])
        current['receipt'] = None
        self.assertEqual('unknown', liveness.classify(saved, current)['kind'])

    def test_live_provider_with_checked_dead_owner_is_unsupervised_even_with_live_keeper(self):
        saved = metadata(); result = liveness.classify(saved, inspection(saved, owner=False))
        self.assertEqual(('unsupervised', 'owner_not_alive'), (result['kind'], result['reason']))
        self.assertEqual({'checked': True, 'alive': False}, result['owner'])
        self.assertIs(result['keeper']['alive'], True)
        self.assertIs(result['provider']['alive'], True)

    def test_live_provider_with_checked_dead_keeper_is_unsupervised(self):
        saved = metadata(); current = inspection(saved, keeper=False)
        current['receipt'] = None
        result = liveness.classify(saved, current)
        self.assertEqual(('unsupervised', 'keeper_not_alive'), (result['kind'], result['reason']))

    def test_owner_loss_and_deadline_receipts_mark_interrupted_during_or_after_cleanup(self):
        saved = metadata()
        for cause in ('owner_lost', 'stage_deadline'):
            for phase in ('stopping', 'stopped'):
                for alive in (True, False):
                    with self.subTest(cause=cause, phase=phase, provider_alive=alive):
                        current = inspection(saved, owner=False, provider=alive, phase=phase, cause=cause)
                        result = liveness.classify(saved, current)
                        self.assertEqual(('interrupted', cause), (result['kind'], result['reason']))
                        self.assertIs(result['provider']['alive'], alive)

    def test_valid_interruption_receipt_survives_denied_inspection_without_claiming_process_death(self):
        saved = metadata()
        for phase in ('stopping', 'uncertain'):
            for cause in ('owner_lost', 'stage_deadline'):
                with self.subTest(phase=phase, cause=cause):
                    current = inspection(saved, phase=phase, cause=cause, error='access denied')
                    for role in liveness.ROLES:
                        current[role] = {'checked': False, 'alive': None}
                    result = liveness.classify(saved, current)
                    self.assertEqual(('interrupted', cause), (result['kind'], result['reason']))
                    self.assertTrue(all(result[role] == {'checked': False, 'alive': None} for role in liveness.ROLES))
                    self.assertEqual('access denied', result['receipt']['cleanup_error'])

    def test_normal_stop_requires_observed_dead_provider_and_intact_cleanup_receipt(self):
        saved = metadata()
        for cause in ('controller_finished', 'provider_stopped'):
            with self.subTest(cause=cause):
                current = inspection(saved, keeper=False, provider=False, phase='stopped', cause=cause)
                self.assertEqual('stopped', liveness.classify(saved, current)['kind'])
                current['provider'] = {'checked': False, 'alive': False}
                self.assertEqual('unknown', liveness.classify(saved, current)['kind'])

    def test_dead_provider_without_stop_receipt_is_unknown_not_completion(self):
        saved = metadata(); current = inspection(saved, provider=False)
        self.assertEqual('unknown', liveness.classify(saved, current)['kind'])
        current['receipt'] = None
        self.assertEqual('unknown', liveness.classify(saved, current)['kind'])

    def test_discharge_is_not_provider_exit_and_cleanup_error_is_not_verified_stop(self):
        saved = metadata(); current = inspection(saved, phase='discharged', cause='controller_finished')
        self.assertNotEqual('stopped', liveness.classify(saved, current)['kind'])
        current = inspection(saved, provider=False, phase='stopped', cause='controller_finished', error='cleanup failed')
        self.assertEqual('unknown', liveness.classify(saved, current)['kind'])

    def test_receipt_from_another_nonce_or_birth_identity_is_not_trusted(self):
        saved = metadata()
        for key in ('nonce', 'owner', 'keeper', 'provider'):
            with self.subTest(binding=key):
                current = inspection(saved, provider=False, phase='stopped', cause='owner_lost')
                if key == 'nonce':
                    current['receipt'][key] = 'b' * 32
                else:
                    current['receipt'][key]['birth_identity'] += 1
                result = liveness.classify(saved, current)
                self.assertEqual('unknown', result['kind'])
                self.assertIsNone(result['receipt'])

    def test_provider_inventory_uses_stable_identity_when_display_metadata_changes(self):
        saved = metadata()
        saved['provider'].update(started='initial display time', birth_time=125.3, group=103)
        for phase, cause, provider, expected in (('armed', None, True, 'supervised'),
                ('stopping', 'stage_deadline', True, 'interrupted'),
                ('stopped', 'provider_stopped', False, 'stopped')):
            with self.subTest(phase=phase):
                current = inspection(saved, phase=phase, cause=cause, provider=provider)
                current['receipt']['processes'][0].update(
                    started='refreshed display time', birth_time=125.4, group=104)
                self.assertEqual(expected, liveness.classify(saved, current)['kind'])
                current['receipt']['processes'][0]['birth_identity'] += 1
                self.assertEqual('unknown', liveness.classify(saved, current)['kind'])
                self.assertIsNone(liveness.classify(saved, current)['receipt'])

    def test_receipt_shape_cannot_invent_a_stop_or_an_interruption(self):
        saved = metadata()
        for field, value in (('phase', []), ('cause', {}), ('schema', True), ('processes', []),
                             ('processes', [saved['provider'], saved['provider']]), ('observed_at', ''),
                             ('cleanup_error', False)):
            with self.subTest(field=field, value=value):
                current = inspection(saved, provider=False, phase='stopped', cause='owner_lost')
                current['receipt'][field] = value
                self.assertEqual('unknown', liveness.classify(saved, current)['kind'])
        current = inspection(saved, provider=False, phase='stopped', cause='owner_lost')
        current['receipt'].pop('processes')
        self.assertEqual('unknown', liveness.classify(saved, current)['kind'])

    def test_process_identity_requires_pid_and_birth_identity(self):
        for identity in ({'pid': 101}, {'pid': True, 'birth_identity': 123},
                         {'pid': 101, 'birth_identity': float('nan')},
                         {'pid': 101, 'birth_identity': float('inf')},
                         {'pid': 101, 'birth_identity': False}):
            with self.subTest(identity=identity):
                saved = metadata(); saved['owner'] = identity
                self.assertEqual('unknown', liveness.classify(saved, inspection(saved))['kind'])

    def test_projection_is_independent_of_input_and_returned_nested_values_are_copied(self):
        saved = metadata(); current = inspection(saved)
        before = copy.deepcopy((saved, current))
        result = liveness.classify(saved, current)
        self.assertEqual(before, (saved, current))
        result['owner']['alive'] = False
        result['receipt']['processes'][0]['birth_identity'] += 5
        self.assertEqual(before, (saved, current))


class LivenessViewTests(unittest.TestCase):
    def test_saved_running_and_terminal_status_without_inspection_remain_unknown(self):
        for status in ('RUNNING', 'TASK_COMPLETE', 'PAUSED_INTERRUPTED'):
            for active in ({}, {'supervision': metadata()}):
                with self.subTest(status=status, active=active):
                    state = {'status': status, 'active_stage': active}
                    result = run_view.view(state)
                    self.assertEqual(status, result['status'])
                    self.assertEqual(status == 'TASK_COMPLETE', result['done'])
                    self.assertEqual('unknown', result['liveness']['kind'])

    def test_malformed_active_stage_keeps_running_status_usable_and_liveness_unknown(self):
        for active in (None, 'terra', ['terra'], True, 42):
            with self.subTest(active=active):
                state = {'status': 'RUNNING', 'active_stage': active}
                before = copy.deepcopy(state)
                result = run_view.view(state)
                self.assertEqual('RUNNING', result['status'])
                self.assertEqual('unknown', result['liveness']['kind'])
                self.assertIn('usage', result)
                self.assertEqual(before, state)

    def test_liveness_is_additive_and_does_not_change_old_status_or_action_fields(self):
        saved = metadata(); state = {'status': 'RUNNING', 'phase': 'build', 'next_stage': 'terra',
            'iteration': 4, 'current_task': {'id': 'T4', 'objective': 'Build', 'milestone_id': 'M1'},
            'active_stage': {'supervision': saved}}
        before = copy.deepcopy(state)
        baseline = run_view.view(state); baseline.pop('liveness')
        for current in (inspection(saved), inspection(saved, owner=False),
                        inspection(saved, owner=False, phase='stopped', cause='owner_lost')):
            with self.subTest(inspection=current):
                result = run_view.view(state, liveness=current)
                self.assertEqual(liveness.classify(saved, current), result.pop('liveness'))
                self.assertEqual(baseline, result)
                self.assertEqual(('RUNNING', False, {'kind': 'continue'}),
                                 (result['status'], result['done'], result['needs']))
        self.assertEqual(before, state)

    def test_view_does_not_use_saved_liveness_as_a_fresh_inspection(self):
        saved = metadata(); state = {'status': 'RUNNING', 'active_stage': {'supervision': saved},
                                    'liveness': inspection(saved)}
        result = run_view.view(state)
        self.assertEqual('unknown', result['liveness']['kind'])
        result = run_view.view(state, liveness=inspection(saved))
        self.assertEqual('supervised', result['liveness']['kind'])
        result['liveness']['receipt']['owner']['pid'] = 999
        self.assertEqual(101, saved['owner']['pid'])


    def test_runner_check_exposes_its_own_fresh_liveness_without_claiming_a_provider(self):
        saved = metadata()
        state = {'status': 'RUNNING', 'next_stage': 'sol', 'active_runner_check': {
            'stage': 'regression_proof', 'summary': 'Testing', 'command': 'python -m unittest -v',
            'output': '/run/suite.log', 'supervision': saved, 'liveness': inspection(saved)}}
        before = copy.deepcopy(state)
        result = run_view.view(state)
        self.assertEqual('unknown', result['runner_check']['liveness']['kind'])
        self.assertEqual(saved, result['runner_check']['supervision'])
        for current in (inspection(saved), inspection(saved, owner=False),
                        inspection(saved, owner=False, keeper=False, provider=False,
                                   phase='stopped', cause='owner_lost')):
            with self.subTest(inspection=current):
                result = run_view.view(state, runner_check_liveness=current)
                self.assertEqual(liveness.classify(saved, current), result['runner_check']['liveness'])
                self.assertEqual('unknown', result['liveness']['kind'])
                self.assertEqual(('RUNNING', False, {'kind': 'continue'}),
                                 (result['status'], result['done'], result['needs']))
        result['runner_check']['supervision']['owner']['pid'] = 999
        self.assertEqual(before, state)

    def test_runner_check_with_explicit_invalid_metadata_stays_unknown(self):
        for saved in (None, {}, False, 'invalid'):
            with self.subTest(supervision=saved):
                state = {'status': 'RUNNING', 'active_runner_check': {'supervision': saved}}
                result = run_view.view(state, runner_check_liveness={'owner': {'checked': True, 'alive': False}})
                self.assertEqual('unknown', result['runner_check']['liveness']['kind'])
                self.assertIsNone(result['runner_check']['liveness']['owner']['alive'])


if __name__ == '__main__':
    unittest.main()
