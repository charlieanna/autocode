"""Runner boundary integration, with no live models or autonomous code writes."""
import copy
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import test_autocode as base
from . import test_report_repair as repairs
from goal_fixtures import approve_fixture, assert_operational_wait, body, envelope

runner, support = base.runner, base.s
# The exact module instance runner.autopilot itself dispatches through: importing
# tools.units.autoresolver directly here would load a second, package-relative
# copy whose autocode_support.Paused is a different class than the one raised
# through runner's own bare (sys.path) import chain.
diagnosis_unit = runner.autopilot.unit_module('astra_diagnose')


class ResolverRuntimeTests(unittest.TestCase):
    queue = repairs.RepairTests.queue

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def boundary(self):
        return runner.resolver_runtime.boundary(runner, self.state, self.run, self.root)

    def repeated_terra_failure(self):
        """A repeated, report-repair-exhausted Builder failure, paused as such."""
        pending = self.queue()
        entry = self.state['failure_history'][pending['original']['failure_key']]
        entry['count'] = entry['streak'] = 3
        entry['last_error'] = 'Missing summary'
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        return pending['original']

    def test_report_repair_gate_is_durable_idempotent_and_runner_owned(self):
        self.queue()
        original_goal = copy.deepcopy(self.state['goal_contract'])
        self.assertTrue(self.boundary())
        saved = support.read(self.run / 'state.json')
        outcome = saved['stages'][-1]
        self.assertTrue(outcome['runner_owned'])
        self.assertEqual('runner', outcome['engine'])
        self.assertEqual(0, outcome['runner_calls'])
        self.assertNotIn('command', outcome)
        self.assertEqual('retry', outcome['decision']['action'])
        self.assertEqual(1, outcome['receipt']['attempt'])
        self.state = saved
        self.boundary()
        self.assertEqual(len(saved['stages']), len(support.read(self.run / 'state.json')['stages']))
        self.assertEqual([1], list(self.state['resolver']['attempts'].values()))
        self.assertEqual(original_goal, self.state['goal_contract'])

    def test_execute_repair_reaches_resolver_before_only_report_launch(self):
        self.queue()
        with patch.object(runner, 'run_role', side_effect=RuntimeError('offline stop')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'offline stop'):
                runner.execute_report_repair(self.state, self.run, self.root)
        self.assertTrue(self.state['stages'][-1]['runner_owned'])
        self.assertTrue(launch.call_args.kwargs['report_only'])
        self.assertFalse(launch.call_args.kwargs['allow_write'])

    def test_repeated_failure_caps_resolver_retry_after_restart(self):
        self.queue()
        self.boundary()
        entry = next(iter(self.state['failure_history'].values()))
        entry['count'] = entry['streak'] = 3
        support.atomic_json(self.run / 'state.json', self.state)
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as caught:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        launch.assert_not_called()

    def test_resolver_attempt_budget_survives_reload(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.state = support.read(self.run / 'state.json')
        # Even if another path resets the local repair counter, a third distinct
        # request for the same blocker cannot reset the resolver's durable budget.
        self.state['pending_report_repair']['attempts'] = 0
        self.state['pending_report_repair']['error'] = 'different error wording'
        with self.assertRaises(support.Paused):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_distinct_validator_originals_have_distinct_budgets(self):
        self.queue(role='sol', stage='sol', task_id='milestone-task')
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        spent = copy.deepcopy(self.state['resolver']['attempts'])
        # The public CLI regression exercises successful acceptance between these originals.
        self.state.pop('pending_report_repair')
        self.queue(role='sol', stage='sol', iteration=6, task_id='final-validation-task')
        self.assertTrue(self.boundary())
        self.assertEqual([1, 2], sorted(self.state['resolver']['attempts'].values()))
        for key, count in spent.items():
            self.assertEqual(count, self.state['resolver']['attempts'][key])

    def test_original_execution_not_current_task_error_or_repair_identifies_budget(self):
        pending = self.queue(role='sol', stage='sol', task_id='original-task')
        self.boundary()
        pending['attempts'] = 1
        self.boundary()
        self.state = support.read(self.run / 'state.json')
        pending = self.state['pending_report_repair']
        pending['attempts'] = 0
        pending['error'] = 'A different error class and wording'
        pending['latest_rejected'] = {'output': 'another-repair.json', 'failure_key': 'another-error-class'}
        pending['original']['failure_key'] = 'another-error-class'
        self.state['current_task'] = {'id': 'tampered-current-task'}
        with self.assertRaisesRegex(support.Paused, 'attempt budget exhausted'):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_original_identity_is_pure_and_stable_across_archival_and_legacy_fields(self):
        original = self.queue(role='sol', stage='sol', task_id='original-task')['original']
        before = copy.deepcopy(original)
        for old, archived in original['archived_paths'].items():
            for field in ('output', 'events'):
                if before[field] == archived:
                    before[field] = old
        before.pop('archived_paths')
        identify = runner.resolver_runtime.report_repair_identity
        with patch.object(support, 'file_hash', side_effect=AssertionError('identity must be pure')), \
                patch.object(support, 'snapshot', side_effect=AssertionError('identity must be pure')):
            expected = identify(before)
            self.assertEqual(expected, identify(original))
            # Legacy originals without the failure-attempt field use its exact
            # pre-archive identity, not today's repair output or current task.
            del original['failure_attempt']
            del before['failure_attempt']
            self.assertEqual(expected, identify(before))
            self.assertEqual(expected, identify(original))
            self.assertNotEqual(expected, identify({**original, 'task_id': 'other-task'}))
            self.assertNotEqual(expected, identify({**original, 'started_at': 'another-execution'}))

    def legacy_boundary(self):
        original = self.state['pending_report_repair']['original']
        legacy = {'stage': original['stage'], 'artifact_hash': original.get('source_revision'),
                  'failure_key': original.get('failure_key')}
        with patch.object(runner.resolver_runtime, '_report_repair_blocker', return_value=legacy):
            return self.boundary()

    def test_legacy_inflight_incident_preserves_spent_budget_and_cached_receipt(self):
        self.queue(role='sol', stage='sol', task_id='original-task')
        self.legacy_boundary()
        saved = copy.deepcopy(self.state['resolver'])
        self.state = support.read(self.run / 'state.json')
        self.boundary()
        self.assertEqual(saved, self.state['resolver'])
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))
        for key, pair in saved['cache'].items():
            self.assertEqual(pair, self.state['resolver']['cache'][key])
        self.state = support.read(self.run / 'state.json')
        self.state['pending_report_repair'].update(attempts=0, error='new wording')
        with self.assertRaisesRegex(support.Paused, 'attempt budget exhausted'):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_legacy_exhausted_collision_is_not_a_charge_against_new_original(self):
        self.queue(role='sol', stage='sol', task_id='milestone-task')
        self.legacy_boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.legacy_boundary()
        spent = copy.deepcopy(self.state['resolver']['attempts'])
        self.state.pop('pending_report_repair')
        self.queue(role='sol', stage='sol', task_id='final-task', iteration=6)
        with self.assertRaisesRegex(support.Paused, 'attempt budget exhausted'):
            self.legacy_boundary()
        self.assertIsNone(self.state['stages'][-1]['receipt']['attempt'])
        self.state = support.read(self.run / 'state.json')
        self.state['status'] = 'RUNNING'
        self.assertTrue(self.boundary())
        self.assertEqual([1, 2], sorted(self.state['resolver']['attempts'].values()))
        for key, count in spent.items():
            self.assertEqual(count, self.state['resolver']['attempts'][key])

    def test_legacy_charges_without_complete_binding_history_fail_closed(self):
        self.queue(role='sol', stage='sol', task_id='original-task')
        self.legacy_boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.legacy_boundary()
        baseline = support.read(self.run / 'state.json')
        for history in ('absent', 'empty', 'original_only', 'receipts_only'):
            for changed_key, lost_failures in ((False, False), (True, False), (True, True)):
                with self.subTest(history=history, changed_key=changed_key, lost_failures=lost_failures):
                    self.state = copy.deepcopy(baseline)
                    if history == 'absent':
                        self.state.pop('stages')
                    else:
                        self.state['stages'] = [row for row in self.state['stages']
                            if (history == 'original_only' and not row.get('runner_owned'))
                            or (history == 'receipts_only' and row.get('runner_owned'))]
                    self.state['pending_report_repair'].update(attempts=0, error='different repair failure')
                    if changed_key:
                        self.state['pending_report_repair']['original']['failure_key'] = 'reclassified-error'
                    if lost_failures:
                        self.state.pop('failure_history', None)
                    ledger = copy.deepcopy(self.state['resolver'])
                    with self.assertRaisesRegex(support.Paused, 'legacy report-repair') as caught:
                        self.boundary()
                    self.assertEqual('PAUSED_RESOLVER_STATE', caught.exception.status)
                    self.assertEqual(ledger, self.state['resolver'])

    def test_legacy_changed_error_key_cannot_reset_same_execution_budget(self):
        self.queue(role='sol', stage='sol', task_id='original-task')
        self.legacy_boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.legacy_boundary()
        self.state = support.read(self.run / 'state.json')
        self.state['pending_report_repair'].update(attempts=0, error='different repair failure')
        self.state['pending_report_repair']['original']['failure_key'] = 'changed-error-class'
        self.state['current_task'] = {'id': 'changed-current-task'}
        with self.assertRaisesRegex(support.Paused, 'attempt budget exhausted'):
            self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_new_original_cannot_discard_unattributed_legacy_charges(self):
        self.queue(role='sol', stage='sol', task_id='milestone-task')
        self.legacy_boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.legacy_boundary()
        self.state.pop('pending_report_repair')
        self.queue(role='sol', stage='sol', task_id='final-task', iteration=6)
        baseline = copy.deepcopy(self.state)
        for missing in ('original', 'one_receipt', 'all_receipts', 'cache'):
            with self.subTest(missing=missing):
                self.state = copy.deepcopy(baseline)
                if missing == 'original':
                    del self.state['stages'][0]
                elif missing == 'one_receipt':
                    del self.state['stages'][1]
                elif missing == 'all_receipts':
                    self.state['stages'] = [row for row in self.state['stages'] if not row.get('runner_owned')]
                else:
                    self.state['resolver']['cache'] = {}
                before = copy.deepcopy(self.state['resolver'])
                with self.assertRaisesRegex(support.Paused, 'legacy report-repair'):
                    self.boundary()
                self.assertEqual(before, self.state['resolver'])

    def test_unrelated_legacy_blocker_does_not_block_new_original_without_history(self):
        self.queue(role='terra', stage='terra', task_id='builder-task')
        self.legacy_boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.legacy_boundary()
        spent = copy.deepcopy(self.state['resolver']['attempts'])
        self.state.pop('pending_report_repair')
        self.queue(role='sol', stage='sol', task_id='validator-task')
        self.state['stages'] = []
        self.assertTrue(self.boundary())
        self.assertEqual([1, 2], sorted(self.state['resolver']['attempts'].values()))
        for key, count in spent.items():
            self.assertEqual(count, self.state['resolver']['attempts'][key])

    def test_permissions_goal_changes_and_untyped_blockers_remain_user_owned(self):
        original = copy.deepcopy(self.state)
        human = runner.lifecycle.human
        for kind in ('permission', 'goal_change', 'clarification', 'blocker'):
            with self.subTest(kind=kind):
                self.state = copy.deepcopy(original)
                request = {'kind': kind, 'decision_needed': 'Make a material decision', 'impact': 'Changes work',
                           'discovered': 'Needs decision', 'options': ['yes', 'no'], 'proposed_delta': ''}
                runner.lifecycle.wait_for_user(self.state, request)
                # A queued request is not yet answerable and the runtime boundary cannot act on it.
                self.assertEqual('RESOLVER_PENDING', self.state['status'])
                queued = copy.deepcopy(self.state)
                self.assertFalse(self.boundary())
                self.assertEqual(queued, self.state)
                action = human.evaluate(self.state)
                if kind in ('permission', 'goal_change'):
                    # Protected decisions are published to the user, never resolved autonomously.
                    self.assertEqual('escalate', action)
                    self.assertEqual('WAITING_FOR_USER', self.state['status'])
                    self.assertEqual(kind, human.current(self.state)['scope'])
                else:
                    # Untyped blockers need a real AutoResolver diagnosis before a human request;
                    # they stay unpublished and still grant no execution.
                    self.assertEqual('defer', action)
                    self.assertEqual('RESOLVER_PENDING', self.state['status'])
                    self.assertIsNone(human.current(self.state))
                    self.assertEqual('blocker', self.state[human.PRIVATE]['scope'])
                before = copy.deepcopy(self.state)
                self.assertEqual(kind in ('permission', 'goal_change'), self.boundary())
                self.assertEqual(before, self.state)
                self.assertEqual(original['goal_contract'], self.state['goal_contract'])
                self.assertEqual(original['stages'], self.state['stages'])
                self.assertNotIn('attempts', self.state['resolver'])

    def test_no_resolver_during_active_operation_or_unapproved_goal(self):
        self.queue()
        self.state['active_stage'] = {'stage': 'terra'}
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)
        self.state.pop('active_stage')
        self.state['goal_contract']['approval_status'] = 'draft'
        # The ledger already holds the approval request published at the writer boundary;
        # an unapproved goal must not add any resolver decision or attempt to it.
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)
        self.assertEqual({'human_escalations'}, set(self.state['resolver']))

    def test_corrupt_saved_ledger_pauses_before_launch(self):
        self.queue()
        self.state['resolver'] = {'cache': {'bad': []}}
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused):
            runner.execute_report_repair(self.state, self.run, self.root)
        launch.assert_not_called()
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])

    def test_blocked_validation_keeps_original_review_route_and_evidence(self):
        record = {'stage': 'sol', 'role': 'sol', 'iteration': 5, 'output': str(self.run / 'sol.json'),
                  'source_revision': support.snapshot(self.root)['revision']}
        support.atomic_json(self.run / 'sol.json', {'verdict': 'BLOCKED'})
        self.state['stages'].append(record)
        self.state.update(validation={'verdict': 'BLOCKED', 'output': record['output']}, next_stage='astra_review')
        self.boundary()
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual('BLOCKED', self.state['validation']['verdict'])
        self.assertEqual('continue', self.state['stages'][-1]['decision']['action'])

    def test_plain_fail_does_not_record_resolver_or_consume_failure_budget(self):
        self.state.update(validation={'verdict': 'FAIL', 'output': str(self.run / 'sol.json')},
                          next_stage='astra_review')
        self.state['stages'].append({'stage': 'sol', 'output': str(self.run / 'sol.json')})
        before = copy.deepcopy(self.state)
        for _ in range(4):
            self.assertFalse(self.boundary())
        self.assertEqual(before, self.state)

    def test_receipt_loader_accepts_a_saved_receipt_missing_the_version_field(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        del saved['cache'][key][1]['version']
        ledger = runner.resolver_runtime.load_ledger(saved)
        _, receipt = next(iter(ledger.cache.values()))
        self.assertEqual(1, receipt.version)

    def test_receipt_loader_rejects_a_retyped_callbacks_used(self):
        # A dataclass constructor accepts Receipt(callbacks_used=[]) without
        # complaint; only the explicit type check catches this reinterpretation.
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['callbacks_used'] = []
        with self.assertRaisesRegex(ValueError, 'callbacks_used'):
            runner.resolver_runtime.load_ledger(saved)

    def test_receipt_loader_rejects_an_unsupported_version(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['version'] = 99
        with self.assertRaisesRegex(ValueError, 'version'):
            runner.resolver_runtime.load_ledger(saved)

    def test_corrupt_receipt_still_pauses_before_launch(self):
        self.queue()
        self.boundary()
        saved = self.state['resolver']
        key = next(iter(saved['cache']))
        saved['cache'][key][1]['callbacks_used'] = []
        support.atomic_json(self.run / 'state.json', self.state)
        self.state = support.read(self.run / 'state.json')
        with patch.object(runner, 'run_role') as launch, self.assertRaises(support.Paused) as caught:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual('PAUSED_RESOLVER_STATE', caught.exception.status)
        launch.assert_not_called()

    def test_explicit_resume_records_epoch_and_accumulates_a_lifetime_total_it_never_resets(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 1
        self.boundary()
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))
        events_before = len(self.state.get('user_events', []))
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual({}, self.state['resolver']['attempts'])
        self.assertEqual(2, self.state['resolver']['lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))
        epoch = self.state['user_events'][-1]
        self.assertEqual('resolver_resume_epoch', epoch['kind'])
        self.assertEqual(2, epoch['cleared_total'])
        self.assertEqual(2, epoch['lifetime_attempts'])
        # A resume with nothing left to clear is a true no-op: no duplicate
        # event, and the lifetime total is never itself reset by this function.
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual(2, self.state['resolver']['lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))

    def test_reset_for_resume_is_a_no_op_when_no_resolver_state_exists(self):
        # Goal approval now records its published request in the resolver ledger, so check both
        # a state with no resolver ledger at all and one that has no resolver attempts.
        bare = copy.deepcopy(self.state)
        bare.pop('resolver')
        before = copy.deepcopy(bare)
        runner.resolver_runtime.reset_for_resume(bare)
        self.assertEqual(before, bare)
        before = copy.deepcopy(self.state)
        runner.resolver_runtime.reset_for_resume(self.state)
        self.assertEqual(before['user_events'], self.state['user_events'])
        self.assertEqual(before['resolver']['human_escalations'], self.state['resolver']['human_escalations'])
        self.assertEqual({}, self.state['resolver'].get('attempts', {}))
        self.assertNotIn('lifetime_attempts', self.state['resolver'])
        self.assertEqual({k: v for k, v in before.items() if k != 'resolver'},
                         {k: v for k, v in self.state.items() if k != 'resolver'})

    def test_explicit_resume_records_report_repair_epoch_and_accumulates_lifetime_total(self):
        self.queue()
        self.boundary()
        self.state['pending_report_repair']['attempts'] = 3
        events_before = len(self.state.get('user_events', []))
        runner.reset_report_repair_for_resume(self.state)
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        self.assertEqual(3, self.state['report_repair_lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))
        epoch = self.state['user_events'][-1]
        self.assertEqual('report_repair_resume_epoch', epoch['kind'])
        self.assertEqual(3, epoch['cleared_attempts'])
        runner.reset_report_repair_for_resume(self.state)
        self.assertEqual(3, self.state['report_repair_lifetime_attempts'])
        self.assertEqual(events_before + 1, len(self.state['user_events']))


class OperationalDiagnosisTests(unittest.TestCase):
    """The astra_diagnose route: admission, model completion, and their shared budget."""
    queue = repairs.RepairTests.queue
    repeated_terra_failure = ResolverRuntimeTests.repeated_terra_failure

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)

    def admit(self):
        return runner.resolver_runtime.admit_operational_diagnosis(runner, self.state, self.run, self.root)

    def test_admission_requires_paused_repeated_failure_status(self):
        self.queue()
        with self.assertRaisesRegex(ValueError, 'paused for repeated failure'):
            self.admit()

    def test_admission_requires_a_stopped_report_repair_identity(self):
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'retry-failed-stage'):
            self.admit()

    def test_admission_is_scoped_to_a_terra_failure(self):
        self.queue(role='sol', stage='sol')
        pending = self.state['pending_report_repair']
        entry = self.state['failure_history'][pending['original']['failure_key']]
        entry['count'] = entry['streak'] = 3
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'scoped to a repeated Builder'):
            self.admit()

    def test_admission_requires_actual_repetition(self):
        self.queue()
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        with self.assertRaisesRegex(ValueError, 'No unchanged repeated failure'):
            self.admit()

    def test_admission_dispatches_astra_diagnose_without_yet_charging_the_run_level_cap(self):
        # Admission alone does not guarantee astra_diagnose ever launches, so
        # it must not charge the run-level cap; charge_diagnostic_dispatch does
        # that at actual dispatch (see the ChargeDiagnosticDispatch tests).
        record = self.repeated_terra_failure()
        self.admit()
        self.assertEqual('astra_diagnose', self.state['next_stage'])
        self.assertEqual('RUNNING', self.state['status'])
        self.assertNotIn('diagnostic_calls', self.state['resolver'])
        request = self.state['diagnosis_request']
        self.assertEqual('terra', request['original_stage'])
        self.assertEqual(record['failure_key'], request['failure_key'])
        self.assertEqual(3, request['repeated_count'])
        outcome = self.state['stages'][-1]
        self.assertTrue(outcome['runner_owned'])
        self.assertEqual('retry', outcome['decision']['action'])
        self.assertEqual(1, outcome['receipt']['attempt'])
        saved = support.read(self.run / 'state.json')
        self.assertEqual('astra_diagnose', saved['next_stage'])
        # The superseded report-repair pointer is archived, not left dangling
        # against a next_stage its own stale-route check would now reject.
        self.assertNotIn('pending_report_repair', self.state)
        self.assertEqual('Superseded by an admitted operational diagnosis',
                         self.state['report_repair_archive'][-1]['reason'])

    def test_admission_exhausted_run_level_cap_escalates_without_dispatch(self):
        self.repeated_terra_failure()
        self.state.setdefault('resolver', {})['diagnostic_calls'] = runner.resolver_runtime.diagnostic_call_limit(self.state)
        with self.assertRaises(support.Paused) as caught:
            self.admit()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertNotIn('diagnosis_request', self.state)
        self.assertEqual('escalate', self.state['stages'][-1]['decision']['action'])

    def test_admission_is_not_reentrant_once_status_leaves_the_pause(self):
        self.repeated_terra_failure()
        self.admit()
        # A second admission attempt is refused by the status check alone.
        with self.assertRaisesRegex(ValueError, 'paused for repeated failure'):
            self.admit()

    def test_finish_accepts_retry_and_retains_the_failure_identity(self):
        record = self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'retry', 'rationale': 'Missing summary field; add it explicitly.',
                                    'guidance': 'Include a nonempty summary before resubmitting.'})
        self.assertTrue(result)
        self.assertEqual('terra', self.state['next_stage'])
        self.assertEqual('RUNNING', self.state['status'])
        self.assertNotIn('diagnosis_request', self.state)
        history = self.state['failure_history'][record['failure_key']]
        self.assertEqual(3, history['count'])
        self.assertEqual(1, len(history['diagnostic_retries']))
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_rejects_escalate_and_preserves_the_failure_identity(self):
        # Must not raise: this function's own _evaluate call already mutated
        # the (candidate) state, and autopilot.apply_result's deep-copy-then-
        # commit pattern discards every candidate mutation the moment
        # anything raises out of it, silently losing the spent evaluation.
        record = self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'escalate', 'rationale': 'Cause is unclear; needs a human decision.'})
        self.assertFalse(result)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        self.assertIn('diagnosis_request', self.state)
        self.assertIn(record['failure_key'], self.state['failure_history'])
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_shares_the_two_attempt_budget_with_admission(self):
        self.repeated_terra_failure()
        self.admit()
        result = runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'retry', 'rationale': ''})  # malformed: empty rationale rejected below
        self.assertFalse(result)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        # The malformed proposal still consumed the second and final evaluation.
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))

    def test_finish_on_the_real_apply_result_path_commits_an_escalate_durably(self):
        """The production exception path, not a direct mutable-state call:
        proves the escalate outcome survives autopilot.apply_result's
        deep-copy-then-commit boundary rather than being discarded."""
        record = self.repeated_terra_failure()
        self.admit()
        diag_record = {'role': 'astra', 'stage': 'astra_diagnose', 'iteration': self.state['iteration'],
                       'output': str(self.run / 'astra_diagnose.json'), 'source_revision':
                       self.state['diagnosis_request']['source_revision'], 'changed_files': [], 'duration_seconds': 0.1}
        value = {'diagnosis': 'Unclear cause after inspection.',
                 'recommendation': {'action': 'escalate', 'rationale': 'Cause is unclear; needs a human decision.'}}
        runner.autopilot.apply_result(runner, self.state, 'astra_diagnose', value, diag_record, self.root, self.run)
        self.assertEqual('PAUSED_REPEATED_FAILURE', self.state['status'])
        self.assertIn('diagnosis_request', self.state)
        self.assertIn(record['failure_key'], self.state['failure_history'])
        # The second evaluation is durably spent: a plain resume must not
        # let a later real dispatch spend a third, uncounted evaluation.
        self.assertEqual([2], list(self.state['resolver']['attempts'].values()))
        # The model's own completion record was saved too, not just the ledger.
        self.assertEqual('astra_diagnose', self.state['stages'][-1]['stage'])

    def test_diagnostic_call_limit_rejects_an_out_of_range_setting(self):
        self.state['settings']['operational_diagnosis'] = {'max_calls_per_run': 9}
        with self.assertRaises(ValueError):
            runner.resolver_runtime.diagnostic_call_limit(self.state)

    def charge(self):
        record = self.state.get('active_stage') or {'stage': self.state['next_stage']}
        runner.resolver_runtime.charge_diagnostic_dispatch(runner, self.state, self.run, self.root, record)
        if record.get('diagnostic_reservation_id'):
            self.state['active_stage'] = record
            support.atomic_json(self.run / 'state.json', self.state)

    def archive_timed_out_astra_diagnose_attempt(self):
        """Simulate what automatically_recover_timed_out_stage actually does:
        archive the timed-out attempt into state['stages'] at the same
        iteration, then route back to astra_diagnose for a fresh launch."""
        self.state.setdefault('stages', []).append({**self.state.pop('active_stage'),
            'stage': 'astra_diagnose', 'role': 'astra', 'iteration': self.state.get('iteration'),
            'automatic_recovery': True, 'timed_out': True, 'abandoned': True, 'rejected': True})
        self.state['next_stage'] = 'astra_diagnose'

    def test_charge_dispatch_is_a_no_op_without_an_admitted_diagnosis_request(self):
        before = copy.deepcopy(self.state)
        self.charge()
        self.assertEqual(before, self.state)

    def test_charge_dispatch_charges_exactly_once_at_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.assertNotIn('diagnostic_calls', self.state['resolver'])
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        saved = support.read(self.run / 'state.json')
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])

    def test_charge_dispatch_rechecking_the_same_unlaunched_attempt_is_idempotent(self):
        # Reconciliation of the same reserved record must not spend again.
        self.repeated_terra_failure()
        self.admit()
        self.charge()
        self.charge()
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])

    def test_malformed_diagnostic_accounting_refuses_before_launch(self):
        self.repeated_terra_failure()
        self.admit()
        original = copy.deepcopy(self.state)
        invalid = [
            {'diagnostic_calls': True}, {'diagnostic_calls': -1},
            {'diagnostic_dispatch_charged_through': '1'},
            {'diagnostic_reservations': []},
            {'diagnostic_legacy_calls': -1, 'diagnostic_reservations': []},
            {'diagnostic_legacy_calls': 0, 'diagnostic_reservations': 'not-a-list'},
            {'diagnostic_legacy_calls': 0, 'diagnostic_reservations': ['same', 'same']},
            {'diagnostic_legacy_calls': 0, 'diagnostic_reservations': ['']},
        ]
        for index, fields in enumerate(invalid):
            with self.subTest(fields=fields):
                self.state = copy.deepcopy(original)
                self.state['iteration'] += index
                self.state['resolver'].update(fields)
                with self.provider() as launches, self.assertRaises(ValueError):
                    self.dispatch()
                self.assertEqual([], launches)
                self.assertNotIn('active_stage', self.state)

    def test_charge_dispatch_charges_a_genuine_timeout_relaunch_again(self):
        # A timed-out diagnosis returned no report, so its relaunch is not a repeated
        # experiment (#422). It is a second genuine provider call and is charged again.
        self.repeated_terra_failure()
        self.admit()
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.archive_timed_out_astra_diagnose_attempt()
        self.charge()
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])
        # A third relaunch (the cap is 2) is refused before launch.
        self.archive_timed_out_astra_diagnose_attempt()
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_pauses_at_the_cap_before_any_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.state['resolver']['diagnostic_calls'] = runner.resolver_runtime.diagnostic_call_limit(self.state)
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_REPEATED_FAILURE', caught.exception.status)
        # The exhausted charge is not itself recorded as spent again.
        self.assertEqual(runner.resolver_runtime.diagnostic_call_limit(self.state),
                         self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_survives_a_reload_between_admission_and_launch(self):
        self.repeated_terra_failure()
        self.admit()
        self.state = support.read(self.run / 'state.json')
        self.charge()
        self.state = support.read(self.run / 'state.json')
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])

    def test_charge_dispatch_refuses_a_stale_source_before_charging(self):
        self.repeated_terra_failure()
        self.admit()
        self.state['diagnosis_request']['source_revision'] = 'a-different-revision'
        with self.assertRaises(support.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_STALE_HANDOFF', caught.exception.status)
        self.assertNotIn('diagnostic_calls', self.state['resolver'])

    def test_reservation_survives_iteration_blocker_archive_changes_and_resume(self):
        self.repeated_terra_failure()
        self.admit()
        ids = []
        for iteration in (1, 2):
            self.state['iteration'] = iteration
            self.state['diagnosis_request']['blocker_id'] = f'blocker-{iteration}'
            self.charge()
            ids.append(self.state['active_stage']['diagnostic_reservation_id'])
            self.state = support.read(self.run / 'state.json')
            runner.resolver_runtime.reset_for_resume(self.state)
            self.charge()
            self.assertEqual(iteration, self.state['resolver']['diagnostic_calls'])
            self.state.pop('active_stage')
            self.state['stages'] = []  # identity cannot depend on retained rows
        self.assertEqual(2, len(set(ids)))
        self.state['iteration'] = 3
        with self.assertRaisesRegex(support.Paused, 'budget exhausted'):
            self.charge()

    def test_legacy_counter_watermark_and_concrete_attempts_are_conservative(self):
        self.repeated_terra_failure()
        self.admit()
        baseline = copy.deepcopy(self.state)
        for calls, watermark, count, expected in ((1, 1, 3, 3), (2, 2, 0, 2), (0, 2, 0, 2), (0, 0, 2, 2)):
            with self.subTest(calls=calls, watermark=watermark, count=count):
                self.state = copy.deepcopy(baseline)
                self.state['resolver'].update(diagnostic_calls=calls, diagnostic_dispatch_charged_through=watermark)
                self.state['stages'] += [{'stage': 'astra_diagnose', 'iteration': i, 'output': f'old-{i}.json'}
                                         for i in range(count)]
                with self.assertRaisesRegex(support.Paused, 'budget exhausted'):
                    self.charge()
                self.assertEqual(expected, support.read(self.run / 'state.json')['resolver']['diagnostic_calls'])
        self.state = baseline
        self.state['resolver'].update(diagnostic_calls=1, diagnostic_dispatch_charged_through=1)
        self.charge()
        self.assertEqual(1, self.state['resolver']['diagnostic_legacy_calls'])
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])
        self.state = support.read(self.run / 'state.json')
        self.charge()
        self.assertEqual(2, self.state['resolver']['diagnostic_calls'])

    @contextmanager
    def provider(self, *, value=None, timeout=False, uncertain=False):
        """Exercise real run_role with fake process preflight, creation and wait."""
        value = value if value is not None else {
            'diagnosis': 'The summary field was omitted.',
            'recommendation': {'action': 'retry', 'rationale': 'Include a nonempty summary field.',
                               'guidance': 'Add the missing field.', 'evidence_refs': []}}
        launches = []
        real_popen = runner.subprocess.Popen

        def launch(command, **kwargs):
            if command[0] == 'git':
                return real_popen(command, **kwargs)
            self.assertEqual('codex', command[0])
            checkpoint = support.read(self.run / 'state.json')
            record = checkpoint['active_stage']
            launches.append(record)
            if record['stage'] == 'astra_diagnose':
                self.assertIn(record['diagnostic_reservation_id'], checkpoint['resolver']['diagnostic_reservations'])
                self.assertEqual(len(checkpoint['resolver']['diagnostic_reservations']), checkpoint['resolver']['diagnostic_calls'])
            support.atomic_json(command[command.index('-o') + 1], value)
            events = [{'type': 'thread.started', 'thread_id': record.get('expected_session') or 'diagnosis-session'}]
            if not timeout and not uncertain:
                events.append({'type': 'turn.completed'})
            kwargs['stdout'].write(''.join(json.dumps(event) + '\n' for event in events))
            kwargs['stdout'].flush()
            return SimpleNamespace(pid=99999999)

        with patch.object(runner.subprocess, 'Popen', side_effect=launch), \
             patch.object(runner.processes, 'preflight', return_value=None), \
             patch.object(runner.processes, 'wait_for_stage', return_value=(-15, True) if timeout else (0, False)):
            yield launches

    def dispatch(self):
        return runner.autopilot.dispatch_unit(runner, self.state, 'astra_diagnose', self.root, self.run)

    def cli(self, *options):
        local = {'auth_mode': 'fixture'}
        self.state['settings']['transport_identity'] = local
        self.state['settings'].setdefault('limits', {
            'iteration_ceiling': 18, 'max_seconds': None,
            'no_progress_batches': 3, 'automatic_retries': 0})
        support.atomic_json(self.run / 'state.json', self.state)
        argv = ['autocode.py', '--workspace', str(self.root), '--run-dir', str(self.run), '--resume-paused', *options]
        with tempfile.TemporaryDirectory() as registry_home, \
             patch.dict(os.environ, {'AUTOCODE_HOME': registry_home}), \
             patch.object(sys, 'argv', argv), patch.object(support, 'assert_no_legacy_process'), \
             patch.object(support, 'local_settings', return_value=local):
            return runner.main()

    def test_cli_diagnose_failed_stage_reaches_astra_diagnose_and_retries_terra(self):
        """The real production dispatch path (autocode.main), not a test helper."""
        self.repeated_terra_failure()
        with self.provider() as launches:
            code = self.cli('--diagnose-failed-stage', '--pause-after-stage')
        self.assertEqual(2, code)
        self.assertEqual(['astra_diagnose'], [row['stage'] for row in launches])
        saved = support.read(self.run / 'state.json')
        self.assertEqual('terra', saved['next_stage'])
        self.assertNotIn('diagnosis_request', saved)
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])
        self.assertEqual([2], list(saved['resolver']['attempts'].values()))

    def test_valid_diagnosis_retry_reaches_the_builder_once(self):
        # #422: an operational failure has no source change to propose, so the accepted
        # diagnosis is the new information. Like --retry-failed-stage, it buys one Builder attempt.
        self.repeated_terra_failure()
        with self.provider() as launches:
            self.assertEqual(2, self.cli('--diagnose-failed-stage', '--pause-after-stage'))
            self.state = support.read(self.run / 'state.json')
            self.cli('--pause-after-stage')
        # The fixture's report is not a Builder report; its repair has its own allowance.
        self.assertEqual(['astra_diagnose', 'terra'], [row['stage'] for row in launches][:2])
        self.assertEqual(1, [row['stage'] for row in launches].count('terra'))
        receipt = launches[1]['recovery_novelty']
        self.assertEqual(('repair', 'explicit_retry', 'diagnosis'),
                         (receipt['action'], receipt['reason'], receipt['grant_kind']))
        saved = support.read(self.run / 'state.json')
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])
        self.assertTrue(saved['failure_history'])

    def test_diagnosis_retry_is_one_builder_attempt_that_returns_a_result(self):
        self.repeated_terra_failure()
        self.admit()
        self.assertTrue(runner.resolver_runtime.finish_operational_diagnosis(
            self.state, self.run, {'action': 'retry', 'rationale': 'Missing summary field; add it explicitly.'}))

        def builder(name, state=None):
            record = {'stage': 'terra', 'output': str(self.run / name), 'started_at': name}
            runner.resolver_recovery.admit_dispatch(state or self.state, record, self.root, self.run)
            return record
        # The grant is the runner's accepted outcome, not a plan that only claims one.
        forged = copy.deepcopy(self.state)
        forged['repair_plan']['recommendation']['rationale'] = 'Edited after acceptance.'
        with self.assertRaisesRegex(support.Paused, 'No causal progress'):
            builder('forged.json', forged)
        first = builder('first.json')
        self.assertEqual('diagnosis', first['recovery_novelty']['grant_kind'])
        # A Builder attempt archived after a timeout returned nothing, so it does not spend the retry.
        self.state['stages'].append({**first, 'timed_out': True, 'automatic_recovery': True,
                                     'abandoned': True, 'rejected': True})
        second = builder('second.json')
        self.assertEqual('explicit_retry', second['recovery_novelty']['reason'])
        self.state['stages'].append(second)
        with self.assertRaisesRegex(support.Paused, 'No causal progress'):
            builder('third.json')

    def test_shared_dispatch_real_timeout_recovery_reload_and_replacement_cap(self):
        # Automatic timeout recovery's relaunch is admitted (#422) and still charged.
        self.repeated_terra_failure()
        self.admit()
        with self.provider(timeout=True) as launches:
            for count in (1, 2):
                self.assertIs(runner.autopilot.SKIP, self.dispatch())
                self.state = support.read(self.run / 'state.json')
                self.assertNotIn('active_stage', self.state)
                self.assertEqual(count, self.state['resolver']['diagnostic_calls'])
                self.assertEqual(count, len(self.state['automatic_timeout_recoveries']))
            with self.assertRaisesRegex(support.Paused, 'budget exhausted'):
                self.dispatch()
        self.assertEqual(2, len(launches))
        self.assertEqual(2, len({row['diagnostic_reservation_id'] for row in launches}))

    def test_cli_real_timeout_recovery_stops_at_lifetime_cap(self):
        self.repeated_terra_failure()
        with self.provider(timeout=True) as launches:
            self.assertEqual(2, self.cli('--diagnose-failed-stage'))
        saved = support.read(self.run / 'state.json')
        self.assertEqual(2, len(launches))
        self.assertEqual(2, saved['resolver']['diagnostic_calls'])
        self.assertEqual(2, len(saved['automatic_timeout_recoveries']))
        # The exhausted pause is surfaced as an AutoResolver operational request.
        assert_operational_wait(self, saved, 'PAUSED_REPEATED_FAILURE')
        self.assertNotIn('active_stage', saved)

    def test_cli_new_iteration_source_and_blocker_do_not_reset_lifetime_cap(self):
        blockers = []
        self.state['iteration'] = 1
        with self.provider() as launches:
            for iteration in (1, 2, 3):
                self.assertEqual(iteration, self.state['iteration'])
                (self.root / 'cause-fixed.txt').write_text(f'source revision {iteration}')
                original = self.repeated_terra_failure()
                self.state['failure_history'][original['failure_key']]['last_error'] = f'Missing distinct required field {iteration}'
                blockers.append(original['failure_key'])
                self.assertEqual(2, self.cli('--diagnose-failed-stage', '--pause-after-stage'))
                self.state = support.read(self.run / 'state.json')
                self.assertEqual(min(iteration, 2), self.state['resolver']['diagnostic_calls'])
                if iteration < 3:
                    self.assertEqual('terra', self.state['next_stage'])
                    # Advance via the actual approved review transition, not a
                    # counter reset fabricated by the test between diagnoses.
                    self.state.update(status='RUNNING', next_stage='astra_review')
                    decision = {**base.RetrofitTest.decision(self), **envelope(self.state),
                        'next_task': {'kind': 'implement', 'milestone_id': 'M1',
                                      'requirements': ['Return the approved greeting'],
                                      'acceptance_criteria': ['C1'], 'validation_plan': ['Run greeting checks']}}
                    output = self.run / f'review-{iteration}.json'
                    support.atomic_json(output, decision)
                    runner.commit_stage_result(self.state, 'astra_review', decision,
                        {'stage': 'astra_review', 'role': 'astra', 'iteration': iteration,
                         'output': str(output), 'source_revision': support.snapshot(self.root)['revision']},
                        self.root, self.run)
                    self.state = support.read(self.run / 'state.json')
        self.assertEqual(3, len(set(blockers)))
        self.assertEqual([1, 2], [row['iteration'] for row in launches])
        # The lifetime cap still refuses a third diagnosis; the pause is published as an operational request.
        assert_operational_wait(self, self.state, 'PAUSED_REPEATED_FAILURE')

    def test_cli_rejected_output_resume_does_not_spend_another_diagnosis(self):
        self.repeated_terra_failure()
        self.state['settings']['report_repair'] = {'max_attempts': 0}
        with self.provider(value={}) as launches:
            self.assertEqual(2, self.cli('--diagnose-failed-stage'))
            self.state = support.read(self.run / 'state.json')
            self.assertEqual('PAUSED_INVALID_OUTPUT', self.state['status'])
            self.assertNotIn('active_stage', self.state)
            self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
            self.assertEqual(2, self.cli())
            self.state = support.read(self.run / 'state.json')
            self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
            public = assert_operational_wait(self, self.state, 'PAUSED_NO_PROGRESS')
            self.assertEqual(2, self.cli())
            self.state = support.read(self.run / 'state.json')
            # ... and a further bare resume retains that same request without a new launch.
            self.assertEqual(public, assert_operational_wait(self, self.state, 'PAUSED_NO_PROGRESS'))
        self.assertEqual(1, len(launches))

    def test_uncertain_launch_is_not_refunded_or_replayed_on_reload(self):
        self.repeated_terra_failure()
        self.admit()
        with self.provider(uncertain=True) as launches:
            with self.assertRaises(support.Paused):
                self.dispatch()
            self.state = support.read(self.run / 'state.json')
            reservation = self.state['active_stage']['diagnostic_reservation_id']
            with self.assertRaisesRegex(support.Paused, 'Uncertain stage'):
                runner.reconcile_active(self.state, self.run, self.root)
            self.charge()  # rechecking the saved reservation is idempotent
            with self.assertRaisesRegex(support.Paused, 'Reconcile the active diagnosis'):
                self.dispatch()
        self.assertEqual(1, len(launches))
        self.assertEqual([reservation], self.state['resolver']['diagnostic_reservations'])
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        runner.abandon_stage(self.state, self.run, self.root, runner.attempt_id(self.state['active_stage']))
        self.state = support.read(self.run / 'state.json')
        with self.provider() as replacements:
            self.assertEqual(2, self.cli('--pause-after-stage'))
        self.assertEqual(0, len(replacements))
        self.assertEqual(1, support.read(self.run / 'state.json')['resolver']['diagnostic_calls'])

    def test_crash_after_reservation_checkpoint_before_popen_remains_uncertain(self):
        self.repeated_terra_failure()
        self.admit()
        original_write = runner.write_json

        def crash_after_checkpoint(path, value):
            original_write(path, value)
            if path == self.run / 'state.json' and value.get('active_stage'):
                raise SystemExit('crash between checkpoint and launch')

        with self.provider() as launches, patch.object(runner, 'write_json', side_effect=crash_after_checkpoint):
            with self.assertRaises(SystemExit):
                self.dispatch()
        self.assertEqual([], launches)
        self.state = support.read(self.run / 'state.json')
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.assertIn('diagnostic_reservation_id', self.state['active_stage'])
        with self.provider() as launches:
            self.assertEqual(2, self.cli())
        self.assertEqual([], launches)
        saved = support.read(self.run / 'state.json')
        self.assertIn('active_stage', saved)
        self.assertEqual(1, saved['resolver']['diagnostic_calls'])

    def test_completed_active_reconciliation_is_idempotent_without_relaunch(self):
        self.repeated_terra_failure()
        self.admit()
        with self.provider() as launches, patch.object(runner, 'commit_stage_result', side_effect=SystemExit('crash before apply')):
            with self.assertRaises(SystemExit):
                self.dispatch()
        self.assertEqual(1, len(launches))
        self.state = support.read(self.run / 'state.json')
        with self.provider() as replays:
            runner.reconcile_active(self.state, self.run, self.root)
            self.state = support.read(self.run / 'state.json')
            runner.reconcile_active(self.state, self.run, self.root)
        self.assertEqual([], replays)
        self.assertNotIn('active_stage', self.state)
        self.assertEqual('terra', self.state['next_stage'])
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])

    def test_report_only_repair_has_separate_allowance_not_another_diagnosis(self):
        self.repeated_terra_failure()
        self.state['settings']['operational_diagnosis'] = {'max_calls_per_run': 1}
        self.admit()
        with self.provider(value={}) as launches:
            self.assertIs(runner.autopilot.SKIP, self.dispatch())
        self.assertEqual(1, len(launches))
        self.state = support.read(self.run / 'state.json')
        self.assertEqual(0, self.state['pending_report_repair']['attempts'])
        with self.provider() as repairs_launched:
            runner.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual(1, len(repairs_launched))
        self.assertTrue(repairs_launched[0]['report_only'])
        self.assertNotIn('diagnostic_reservation_id', repairs_launched[0])
        self.assertEqual(1, self.state['resolver']['diagnostic_calls'])
        self.assertEqual('terra', self.state['next_stage'])

    def test_prelaunch_guards_do_not_charge_in_either_dispatch_path(self):
        self.repeated_terra_failure()
        self.admit()
        baseline = copy.deepcopy(self.state)
        guards = ((runner.milestones, 'dispatch_guard', 'PAUSED_MILESTONE_BUDGET'),
                  (runner.workflow, 'dispatch_guard', 'PAUSED_WORKFLOW_CONFLICT'),
                  (runner.goals, 'execution_guard', 'PAUSED_GOAL_UNAPPROVED'),
                  (runner.interventions, 'admission', 'PAUSED_INTERVENTION_PENDING'),
                  (runner, 'timeout_recovery_guard', 'PAUSED_TIMEOUT_RECOVERY'))
        for cli in (False, True):
            for module, method, status in guards:
                with self.subTest(cli=cli, guard=method, status=status):
                    self.state = copy.deepcopy(baseline)
                    with self.provider() as launches, patch.object(module, method, side_effect=support.Paused(status, 'guard refused')):
                        if cli:
                            self.assertEqual(2, self.cli())
                            self.state = support.read(self.run / 'state.json')
                        else:
                            with self.assertRaises(support.Paused) as caught:
                                self.dispatch()
                            self.assertEqual(status, caught.exception.status)
                    self.assertEqual([], launches)
                    self.assertNotIn('diagnostic_calls', self.state['resolver'])
                    self.assertNotIn('active_stage', self.state)

    def test_source_contract_evidence_refused_without_charge(self):
        self.repeated_terra_failure()
        self.admit()
        baseline = copy.deepcopy(self.state)
        for cli in (False, True):
            for field in ('source_revision', 'contract_hash', 'evidence_hashes'):
                with self.subTest(cli=cli, field=field):
                    self.state = copy.deepcopy(baseline)
                    self.state['diagnosis_request'][field] = (
                        {str(self.evidence): 'changed-hash'} if field == 'evidence_hashes' else 'changed')
                    with self.provider() as launches:
                        if cli:
                            self.assertEqual(2, self.cli())
                            self.state = support.read(self.run / 'state.json')
                        else:
                            with self.assertRaisesRegex(support.Paused, 'Diagnosis'):
                                self.dispatch()
                    self.assertEqual([], launches)
                    self.assertNotIn('diagnostic_calls', self.state['resolver'])

    def test_source_drift_during_preparation_is_rechecked_at_launch_admission(self):
        self.repeated_terra_failure()
        self.admit()
        changed = self.root / 'changed-during-preparation.txt'
        with self.provider() as launches, patch.object(runner, 'rotate_if_needed', side_effect=lambda *args: changed.write_text('new source')):
            with self.assertRaisesRegex(support.Paused, 'current source'):
                self.dispatch()
        self.assertEqual([], launches)
        self.assertNotIn('diagnostic_calls', self.state['resolver'])
        self.assertNotIn('active_stage', self.state)


    def test_cli_time_budget_guard_does_not_charge(self):
        self.repeated_terra_failure()
        self.admit()
        self.state['settings']['limits'] = {
            'iteration_ceiling': 18, 'max_seconds': 1,
            'no_progress_batches': 3, 'automatic_retries': 0}
        self.state['active_seconds'] = 2
        with self.provider() as launches:
            self.assertEqual(2, self.cli())
        saved = support.read(self.run / 'state.json')
        assert_operational_wait(self, saved, 'PAUSED_TIME_LIMIT')
        self.assertEqual([], launches)
        self.assertNotIn('diagnostic_calls', saved['resolver'])

    def test_cli_rejects_combining_diagnose_and_retry_failed_stage(self):
        argv = ['autocode.py', '--workspace', str(self.root.resolve()), '--run-dir', str(self.run.resolve()),
                '--resume-paused', '--diagnose-failed-stage', '--retry-failed-stage']
        with patch.object(sys, 'argv', argv):
            with self.assertRaises(SystemExit) as caught:
                runner.main()
        self.assertEqual(2, caught.exception.code)


class OperationalBudgetResumeTests(unittest.TestCase):
    """Public CLI recovery of an answered time-cap pause, without a live provider."""

    def setUp(self):
        base.RetrofitTest.setUp(self)
        runner.lifecycle.migrate(self.state)
        runner.lifecycle.install_draft(self.state, body(), origin='fixture', queue_human=False)
        self.state.update(next_stage='astra_discovery', active_seconds=3870.78)
        self.state['settings'].update(engine='codex', transport_identity={'auth_mode': 'fixture'},
            limits={'iteration_ceiling': 18, 'max_seconds': 3600,
                    'no_progress_batches': 3, 'automatic_retries': 0, 'stage_timeout_seconds': 900,
                    'idle_timeout_seconds': 300, 'tool_timeout_seconds': 1800},
            budget_origins={'max_seconds': 'user_explicit', 'iteration_ceiling': 'user_explicit'})
        self.other_limits = {key: value for key, value in self.state['settings']['limits'].items()
                             if key != 'max_seconds'}
        self.contract = copy.deepcopy(self.state['goal_contract'])
        self.active_seconds = self.state['active_seconds']

    def cli(self, *options):
        support.atomic_json(self.run / 'state.json', self.state)
        argv = ['autocode.py', '--workspace', str(self.root), '--run-dir', str(self.run),
                '--no-chat', *options]
        output = io.StringIO()
        with patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root / '.autocode' / 'registry')}), \
             patch.object(sys, 'argv', argv), patch.object(support, 'assert_no_legacy_process'), \
             patch.object(support, 'local_settings', return_value={'auth_mode': 'fixture'}), \
             patch.object(runner, 'run_role', side_effect=RuntimeError('offline planning admission')) as launch, \
             redirect_stdout(output), redirect_stderr(output):
            code = runner.main()
        self.output = output.getvalue()
        self.state = support.read(self.run / 'state.json')
        return code, launch

    def answered_time_pause(self):
        code, launch = self.cli()
        self.assertEqual(2, code)
        launch.assert_not_called()
        public = assert_operational_wait(self, self.state, 'PAUSED_TIME_LIMIT')
        code, launch = self.cli('--resolver-request', public['request_id'],
            '--resolver-token', public['request_token'], '--resolver-response', 'provide_information',
            '--resolver-message', 'User authorizes another 1800 active seconds, total cap 5400.')
        self.assertEqual(0, code)
        launch.assert_not_called()
        code, launch = self.cli('--resume-paused')
        self.assertEqual(2, code)
        launch.assert_not_called()
        self.assertEqual('PAUSED_TIME_LIMIT', self.state['status'])
        self.assertIsNone(runner.lifecycle.human.current(self.state))
        self.assertEqual(3600, self.state['settings']['limits']['max_seconds'])
        self.assert_preserved()

    def assert_preserved(self, *, stage_seconds=900):
        self.assertEqual(self.active_seconds, self.state['active_seconds'])
        self.assertEqual(self.contract, self.state['goal_contract'])
        self.assertFalse(runner.goals.approved(self.state))
        self.assertEqual('astra_discovery', self.state['next_stage'])
        self.assertEqual({**self.other_limits, 'stage_timeout_seconds': stage_seconds},
                         {key: value for key, value in self.state['settings']['limits'].items()
                          if key != 'max_seconds'})
        self.assertEqual('user_explicit', self.state['settings']['budget_origins']['max_seconds'])
        self.assertNotIn('active_stage', self.state)
        self.assertFalse(any(row.get('stage') == 'terra' for row in self.state['stages']))

    def assert_planning_admitted(self, code, launch):
        self.assertEqual(2, code)  # The fake provider stops immediately at admission.
        self.assertEqual(1, launch.call_count, self.output)
        self.assertEqual('astra', launch.call_args.kwargs['role'])
        self.assertFalse(launch.call_args.kwargs['allow_write'])
        self.assertEqual('read-only', launch.call_args.kwargs['sandbox'])
        self.assertEqual(5400, self.state['settings']['limits']['max_seconds'])
        self.assert_preserved()

    def test_cli_changed_time_cap_after_consumed_response_resumes_only_planning(self):
        self.answered_time_pause()
        self.assert_planning_admitted(*self.cli('--resume-paused', '--max-seconds', '5400'))

    def test_cli_reasserting_persisted_time_cap_resumes_only_planning(self):
        self.answered_time_pause()
        self.persist_stale_time_pause()
        self.assert_planning_admitted(*self.cli('--resume-paused', '--max-seconds', '5400'))

    def persist_stale_time_pause(self):
        # Save the larger cap without resuming, then expose the still-unacknowledged pause.
        # This is the same public CLI sequence used by the independent live fixture.
        for options in (('--max-seconds', '5400'), ('--resume-paused',)):
            code, launch = self.cli(*options)
            self.assertEqual(2, code)
            launch.assert_not_called()
        assert_operational_wait(self, self.state, 'PAUSED_TIME_LIMIT')

    def test_cli_time_pause_requires_matching_unexhausted_explicit_cap(self):
        self.answered_time_pause()
        baseline = copy.deepcopy(self.state)
        for options in (('--resume-paused',),
                        ('--resume-paused', '--max-seconds', '3600'),
                        ('--resume-paused', '--max-stage-seconds', '1200')):
            with self.subTest(options=options):
                self.state = copy.deepcopy(baseline)
                code, launch = self.cli(*options)
                self.assertEqual(2, code)
                self.assertEqual(0, launch.call_count, self.output)
                self.assertIn(self.state['status'], ('PAUSED_TIME_LIMIT', 'WAITING_FOR_USER'))
                self.assertEqual(3600, self.state['settings']['limits']['max_seconds'])
                self.assert_preserved(stage_seconds=1200 if '--max-stage-seconds' in options else 900)

    def test_cli_persisted_time_cap_does_not_make_bare_or_unrelated_resume_authority(self):
        self.answered_time_pause()
        self.persist_stale_time_pause()
        baseline = copy.deepcopy(self.state)
        for options in (('--resume-paused',),
                        ('--resume-paused', '--max-stage-seconds', '1200'),
                        ('--max-seconds', '5400')):
            with self.subTest(options=options):
                self.state = copy.deepcopy(baseline)
                code, launch = self.cli(*options)
                self.assertEqual(2, code)
                self.assertEqual(0, launch.call_count, self.output)
                assert_operational_wait(self, self.state, 'PAUSED_TIME_LIMIT')
                self.assertEqual(5400, self.state['settings']['limits']['max_seconds'])
                self.assert_preserved(stage_seconds=1200 if '--max-stage-seconds' in options else 900)

    def test_cli_reasserting_exhausted_persisted_cap_does_not_reset_elapsed_time(self):
        self.answered_time_pause()
        self.active_seconds = self.state['active_seconds'] = 5400
        self.persist_stale_time_pause()
        code, launch = self.cli('--resume-paused', '--max-seconds', '5400')
        self.assertEqual(2, code)
        launch.assert_not_called()
        assert_operational_wait(self, self.state, 'PAUSED_TIME_LIMIT')
        self.assertEqual(5400, self.state['settings']['limits']['max_seconds'])
        self.assert_preserved()

    def test_cli_time_cap_cannot_acknowledge_a_different_request_scope(self):
        self.answered_time_pause()
        baseline = copy.deepcopy(self.state)
        for scope in ('permission', 'goal_change'):
            with self.subTest(scope=scope):
                self.state = copy.deepcopy(baseline)
                runner.lifecycle.wait_for_user(self.state, {
                    'kind': scope, 'discovered': 'The task needs a separate decision',
                    'impact': 'Changes the permitted work', 'decision_needed': 'Authorize the changed scope?',
                    'options': ['Leave paused', 'Revise the goal'], 'proposed_delta': 'Change permitted work'},
                    origin={'stage': 'astra_discovery'})
                runner.write_json(self.run / 'state.json', self.state)
                self.state = support.read(self.run / 'state.json')
                self.assertEqual(scope, runner.lifecycle.human.current(self.state)['scope'])
                code, launch = self.cli('--resume-paused', '--max-seconds', '5400')
                self.assertEqual(2, code)
                self.assertEqual(0, launch.call_count, self.output)
                self.assertEqual(scope, runner.lifecycle.human.current(self.state)['scope'])
                self.assert_preserved()


class OperationalDiagnosisUnitTests(unittest.TestCase):
    """tools/units/autoresolver.py's astra_diagnose prepare/validate, standalone."""

    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)
        self.state['workspace'] = str(self.root)
        pending = repairs.RepairTests.queue(self)
        record = pending['original']
        self.state.update(status='PAUSED_REPEATED_FAILURE')
        self.state['diagnosis_request'] = {
            'contract_hash': self.state['goal_contract']['hash'],
            'source_revision': support.snapshot(self.root)['revision'],
            'original_stage': 'terra', 'failure_key': record.get('failure_key'), 'blocker_id': 'b1',
            'description': 'Repeated Builder report rejection', 'repeated_count': 3,
            'evidence': [record['output'], record['events']], 'evidence_hashes': {}}
        self.state['next_stage'] = 'astra_diagnose'

    def test_prepare_diagnosis_is_read_only_with_a_bounded_schema(self):
        request = diagnosis_unit.prepare_diagnosis(self.state, 'astra_diagnose', self.run / 'state.json', runner.SCHEMA_DIR)
        self.assertFalse(request.allow_write)
        self.assertEqual({'diagnosis', 'recommendation'}, set(request.schema['required']))
        self.assertEqual(['retry', 'escalate'], request.schema['properties']['recommendation']['properties']['action']['enum'])
        self.assertIn('diagnosis_request', request.prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

    def test_prepare_diagnosis_rejects_the_wrong_stage(self):
        with self.assertRaises(ValueError):
            diagnosis_unit.prepare_diagnosis(self.state, 'astra_resolve', self.run / 'state.json', runner.SCHEMA_DIR)

    def test_validate_diagnosis_rejects_a_missing_diagnosis(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        with self.assertRaises(ValueError):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': '  ', 'recommendation': {
                'action': 'retry', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_rejects_an_invalid_action(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        with self.assertRaises(ValueError):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
                'recommendation': {'action': 'implement', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_rejects_source_drift(self):
        record = {'source_revision': 'a-different-revision', 'changed_files': []}
        with self.assertRaises(support.Paused):
            diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
                'recommendation': {'action': 'escalate', 'rationale': 'x'}}, record, self.root)

    def test_validate_diagnosis_accepts_a_well_formed_recommendation(self):
        record = {'source_revision': self.state['diagnosis_request']['source_revision'], 'changed_files': []}
        diagnosis_unit.validate_diagnosis(self.state, {'diagnosis': 'Looked at the logs',
            'recommendation': {'action': 'retry', 'rationale': 'Add the missing summary field'}}, record, self.root)
