"""Offline planning recovery grants: provenance, admission and human boundaries."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from . import test_autocode as base
from goal_fixtures import approve_fixture

runner, s = base.runner, base.s
resolver, planning = runner.resolver_runtime, runner.planning


class OperationalRecoveryTests(unittest.TestCase):
    def setUp(self):
        base.RetrofitTest.setUp(self)
        self.state['run_dir'] = str(self.run)
        approve_fixture(self.state, runner.goals)
        self.state['goal_contract'].update(approval_status='draft', approval_event=None)
        self.state['settings']['joint_planning'] = True
        self.state.update(next_stage='astra_challenge', status='PAUSED_PLANNING_BUDGET')
        discovery = self.run / 'discovery.json'
        s.atomic_json(discovery, {'summary': 'accepted draft'})
        self.state['stages'] = [{'stage': 'astra_discovery', 'output': str(discovery)}]
        self.state['planning'] = {'astra_calls': 2, 'reports': {
            'astra_discovery': {'output': str(discovery), 'report': {'summary': 'accepted draft'}}}}
        self.origins = [self.timeout(1), self.timeout(2)]

    def timeout(self, number, **changes):
        directory = self.run / f'archived-{number}'
        directory.mkdir()
        snapshot = s.snapshot(self.root)
        for name in ('before', 'after'):
            s.atomic_json(directory / f'{name}.json', snapshot)
        events = directory / 'events.jsonl'
        events.write_text(json.dumps({'type': 'step_start'}) + '\n')
        record = {'stage': 'astra_challenge', 'role': 'astra', 'iteration': number,
                  'output': str(directory / 'report.json'), 'events': str(events),
                  'before_ref': str(directory / 'before.json'), 'after_ref': str(directory / 'after.json'),
                  'source_revision': snapshot['revision'], 'changed_files': [], 'accounted': True,
                  'timed_out': True, 'automatic_recovery': True, 'abandoned': True, 'rejected': True,
                  'exit_code': 1}
        record.update(changes)
        self.state['stages'].append(record)
        self.state.setdefault('reconciliation_notes', []).append({
            'archived': str(directory), 'stage': record['stage'], 'iteration': number})
        return record

    def boundary(self):
        return resolver.operational_boundary(runner, self.state, self.run, self.root)

    def charge(self, stage='astra_challenge', number=3):
        record = {'stage': stage, 'iteration': number, 'output': str(self.run / f'attempt-{number}.json')}
        planning.charge(self.state, stage, record, self.root)
        return record

    def test_draft_grant_is_durable_idempotent_and_never_approval(self):
        contract = copy.deepcopy(self.state['goal_contract'])
        with patch.object(runner, 'run_role') as launch:
            self.assertTrue(self.boundary())
            count = len(self.state['stages'])
            self.state = s.read(self.run / 'state.json')
            self.assertTrue(self.boundary())
            self.assertEqual(count, len(self.state['stages']))
            launch.assert_not_called()
        self.assertEqual(contract, self.state['goal_contract'])
        self.assertFalse(runner.goals.approved(self.state))
        self.assertEqual(2, self.state['planning']['astra_calls'])
        self.assertEqual(2, planning.review_call_limit(self.state))
        receipt = self.state['stages'][-1]
        self.assertTrue(receipt['runner_owned'])
        self.assertEqual('retry', receipt['decision']['action'])
        self.assertEqual([], receipt['receipt']['callbacks_used'])

    def test_accepted_repaired_discovery_still_owns_its_planning_cycle(self):
        self.state['stages'][0].update(stage='astra_discovery_report_repair',
                                       original_stage='astra_discovery', report_only=True, exit_code=0)
        original = copy.deepcopy(self.state['planning']['reports'])
        self.assertTrue(self.boundary())
        self.charge()
        self.assertEqual(3, self.state['planning']['astra_calls'])
        self.assertEqual(1, self.state['planning']['recovery_review_calls_used'])
        self.assertEqual(original, self.state['planning']['reports'])
        self.assertFalse(runner.goals.approved(self.state))

    def test_other_or_rejected_repair_cannot_impersonate_discovery(self):
        original = copy.deepcopy(self.state)
        for changes in ({'stage': 'terra_report_repair', 'original_stage': 'astra_discovery', 'report_only': True},
                        {'stage': 'astra_discovery_report_repair', 'original_stage': 'astra_discovery', 'report_only': False},
                        {'stage': 'astra_discovery_report_repair', 'original_stage': 'astra_discovery', 'report_only': True, 'rejected': True},
                        {'stage': 'astra_discovery_report_repair', 'original_stage': 'astra_discovery', 'report_only': True, 'abandoned': True}):
            with self.subTest(changes=changes):
                self.state = copy.deepcopy(original)
                self.state['stages'][0].update(changes)
                self.assertFalse(self.boundary())
                self.assertEqual(2, self.state['planning']['astra_calls'])

    def test_provider_rate_limit_is_resolver_owned_without_switching_billing(self):
        self.state.update(status='PAUSED_RATE_LIMIT', next_stage='astra_discovery')
        roles = copy.deepcopy(self.state['settings']['roles'])
        error = s.Paused('PAUSED_RATE_LIMIT', 'Provider returned an actual rate limit')
        self.assertTrue(resolver.record_operational_exhaustion(runner, self.state, self.run, error))
        runner.write_json(self.run / 'state.json', self.state)
        public = runner.resolver_human.current(self.state)
        self.assertEqual('operational_exhaustion', public['scope'])
        origin = self.state['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']
        self.assertEqual('PAUSED_RATE_LIMIT', origin['pause_status'])
        self.assertEqual('provider_or_spending_guard', origin['budget']['category'])
        self.assertEqual(roles, self.state['settings']['roles'])
        self.assertNotIn('reviewer_route_fallbacks', self.state['planning'])

    def test_saved_legacy_rate_limit_is_reconciled_before_any_new_provider_call(self):
        self.state.update(status='PAUSED_RATE_LIMIT', next_stage='astra_discovery',
                          stop_reason='Provider returned an actual rate limit')
        s.atomic_json(self.run / 'state.json', self.state)
        with patch.object(runner, 'run_role') as launch, patch.object(sys, 'argv', [
                str(Path(runner.__file__)), '--workspace', str(self.root),
                '--run-dir', str(self.run), '--no-chat']):
            code = runner.main()
        self.assertEqual(2, code)
        launch.assert_not_called()
        saved = s.read(self.run / 'state.json')
        public = runner.resolver_human.current(saved)
        self.assertEqual('operational_exhaustion', public['scope'])
        origin = saved['resolver']['human_escalations'][public['request_id']]['identity']['proposal']['origin']
        self.assertEqual('PAUSED_RATE_LIMIT', origin['pause_status'])

    def test_two_timeouts_fund_challenge_then_final_not_extra_debate(self):
        self.boundary()
        record = self.charge()
        self.state['stages'].append(record)
        s.atomic_json(Path(record['output']), {'summary': 'challenge complete'})
        self.state['planning']['reports']['astra_challenge'] = {
            'output': record['output'], 'report': {'concerns': []}}
        with self.assertRaises(s.Paused) as caught:
            self.boundary()
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)
        self.state['next_stage'] = 'astra_finalize'
        self.assertTrue(self.boundary())
        final = self.charge('astra_finalize', 4)
        self.assertNotEqual(record['planning_recovery_grant'], final['planning_recovery_grant'])
        self.assertEqual(4, self.state['planning']['astra_calls'])
        self.assertEqual(2, self.state['planning']['recovery_review_calls_used'])
        with self.assertRaises(s.Paused) as caught:
            self.boundary()
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)

    def test_consumed_admission_survives_crash_without_refund(self):
        self.boundary()
        record = self.charge()
        self.state['active_stage'] = record
        s.atomic_json(self.run / 'state.json', self.state)
        self.state = s.read(self.run / 'state.json')
        self.assertEqual(3, self.state['planning']['astra_calls'])
        self.assertTrue(self.state['planning']['recovery_review_grants'][0]['consumed'])
        self.assertFalse(self.boundary())
        with self.assertRaises(s.Paused):
            self.charge()
        self.assertEqual(3, self.state['planning']['astra_calls'])

    def test_grant_pin_mutations_fail_before_admission(self):
        self.boundary()
        pristine = copy.deepcopy(self.state)
        mutations = {
            'source': lambda: (self.root / 'changed.py').write_text('changed'),
            'contract': lambda: self.state['goal_contract'].update(hash='different'),
            'route': lambda: self.state['settings']['roles']['astra'].update(model='different'),
            'log': lambda: Path(self.origins[0]['events']).write_text('{"type":"turn.completed"}\n'),
            'input': lambda: Path(self.state['planning']['reports']['astra_discovery']['output']).write_text('{}'),
            'cycle': lambda: self.state.update(planning_history=[{}]),
            'before': lambda: Path(self.origins[0]['before_ref']).write_text('{}'),
            'after': lambda: Path(self.origins[0]['after_ref']).write_text('{}'),
            'receipt': lambda: Path(self.state['planning']['recovery_review_grants'][0]['receipt_output']).write_text('{}'),
        }
        originals = {path: path.read_bytes() for path in (
            Path(self.origins[0]['events']), Path(self.origins[0]['before_ref']), Path(self.origins[0]['after_ref']),
            Path(self.state['planning']['recovery_review_grants'][0]['receipt_output']),
            Path(self.state['planning']['reports']['astra_discovery']['output']))}
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.state = copy.deepcopy(pristine)
                mutate()
                with patch.object(runner, 'run_role') as launch, self.assertRaises(s.Paused) as caught:
                    self.charge()
                self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)
                self.assertEqual(2, self.state['planning']['astra_calls'])
                launch.assert_not_called()
                (self.root / 'changed.py').unlink(missing_ok=True)
                for path, content in originals.items():
                    path.write_bytes(content)

    def test_unproven_timeout_and_old_cycle_are_not_authority(self):
        pristine = copy.deepcopy(self.state)
        for key, value in (('accounted', False), ('automatic_recovery', False), ('timed_out', False),
                           ('changed_files', ['source.py']), ('stage', 'sol'), ('pid', os.getpid())):
            with self.subTest(key=key):
                self.state = copy.deepcopy(pristine)
                for row in self.state['stages'][1:]:
                    row[key] = value
                self.assertFalse(self.boundary())
                self.assertNotIn('recovery_review_grants', self.state['planning'])
        self.state = copy.deepcopy(pristine)
        self.state['stages'].append(self.state['stages'].pop(0))
        self.assertFalse(self.boundary())
        self.state = copy.deepcopy(pristine)
        for row in self.state['stages'][1:]:
            Path(row['events']).write_text('{"type":"turn.completed"}\n')
        self.assertFalse(self.boundary())

    def test_human_requests_and_unreconciled_work_remain_untouched(self):
        pristine = copy.deepcopy(self.state)
        changes = [{'pending_questions': [{'id': 'Q1', 'question': 'Choose scope'}]},
                   {'pending_report_repair': {'original': self.origins[0]}},
                   {'uncertain_artifacts': ['events']}, {'active_stage': self.origins[0]}]
        changes += [{'user_request': {'kind': kind, 'decision_needed': 'Human decides'}}
                    for kind in ('permission', 'goal_change', 'clarification', 'plan_approval')]
        for change in changes:
            with self.subTest(change=change):
                self.state = copy.deepcopy(pristine)
                self.state.update(change)
                before = copy.deepcopy(self.state)
                self.assertFalse(self.boundary())
                self.assertEqual(before, self.state)
        self.state = copy.deepcopy(pristine)
        runner.lifecycle.wait_for_user(self.state, {
            'kind': 'permission', 'decision_needed': 'Allow external access?',
            'impact': 'Exceeds the workspace-only permission boundary',
            'discovered': 'External access needed', 'options': ['allow', 'deny'], 'proposed_delta': ''})
        runner.write_json(self.run / 'state.json', self.state)
        self.assertEqual('permission', runner.resolver_human.current(self.state)['scope'])
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertFalse(resolver.record_operational_exhaustion(
            runner, self.state, self.run, s.Paused('PAUSED_RESOLVER_OPERATIONAL', 'Exhausted')))
        self.assertEqual(before, self.state)
        self.state = copy.deepcopy(pristine)
        (self.run / 'pause-requested').touch()
        before = copy.deepcopy(self.state)
        self.assertFalse(self.boundary())
        self.assertFalse(resolver.record_operational_exhaustion(
            runner, self.state, self.run, s.Paused('PAUSED_RESOLVER_OPERATIONAL', 'Exhausted')))
        self.assertEqual(before, self.state)

    def test_ordinary_allowance_and_hard_guard_are_not_extended(self):
        self.state['planning']['astra_calls'] = 1
        self.assertFalse(self.boundary())
        self.state['planning']['astra_calls'] = 2
        self.state['automatic_recoveries_since_resume'] = runner.MAX_AUTOMATIC_RECOVERIES
        with self.assertRaises(s.Paused) as caught:
            self.boundary()
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY', caught.exception.status)
        self.assertNotIn('recovery_review_grants', self.state['planning'])

    def refundable(self):
        """Mark the timeouts as ordinarily admitted under the refund policy, calls not yet given back."""
        for number, origin in enumerate(self.origins):
            origin['planning_review_charge'] = f'charge-{number}'
        self.state['planning']['review_charges'] = [origin['planning_review_charge'] for origin in self.origins]

    def test_refundable_timeouts_do_not_reserve_extra_planning_credit(self):
        self.refundable()
        count = len(self.state['stages'])
        self.assertFalse(self.boundary())
        # The silent attempts returned no review, so their calls come back before any reserve.
        self.assertEqual(0, self.state['planning']['astra_calls'])
        self.assertTrue(all(origin['planning_review_refunded'] for origin in self.origins))
        self.assertNotIn('recovery_review_grants', self.state['planning'])
        self.assertEqual(count, len(self.state['stages']), 'no resolver receipt without a grant')
        record = self.charge()
        self.assertIn('planning_review_charge', record)
        self.assertNotIn('planning_recovery_grant', record)
        self.assertEqual(1, self.state['planning']['astra_calls'])
        self.assertNotIn('recovery_review_grants', self.state['planning'])

    def test_refunded_timeout_cannot_also_fund_recovery(self):
        # The timeouts gave their calls back; two later reviews that reported spent the allowance.
        for origin in self.origins:
            origin['planning_review_refunded'] = True
        for number in (3, 4):
            self.state['stages'].append({'stage': 'astra_challenge', 'iteration': number, 'exit_code': 0,
                                         'output': str(self.run / f'reported-{number}.json')})
        self.assertFalse(self.boundary())
        self.assertNotIn('recovery_review_grants', self.state['planning'])
        with self.assertRaises(s.Paused) as caught:
            self.charge(number=5)
        self.assertEqual('PAUSED_PLANNING_BUDGET', caught.exception.status)
        self.assertEqual(2, self.state['planning']['astra_calls'])

    def test_sealed_grant_keeps_its_records_and_still_fails_closed(self):
        # A grant sealed by the old order: reserved over calls that were still refundable.
        self.refundable()
        with patch.object(planning, 'refund_unreported'):
            self.assertTrue(self.boundary())
        sealed = copy.deepcopy(self.state)
        self.assertTrue(self.boundary())
        self.assertEqual(sealed, self.state, 'a sealed grant is validated, never reconciled or rewritten')
        source = self.root / 'changed.py'

        def poisoned():
            # What the old admission did next: refund under the grant and admit ordinary retries.
            self.charge(number=3)
            self.charge(number=4)
            self.assertTrue(all(origin.get('planning_review_refunded') for origin in self.state['stages'][1:3]))

        changes = {
            'source': lambda: source.write_text('changed'),
            'evidence': lambda: Path(self.origins[0]['events']).write_text('{"type":"turn.completed"}\n'),
            'user_event': lambda: self.state.setdefault('user_events', []).append({'kind': 'feedback'}),
            'poisoned': poisoned,
        }
        events = Path(self.origins[0]['events']).read_bytes()
        for name, change in changes.items():
            with self.subTest(change=name):
                self.state = copy.deepcopy(sealed)
                change()
                with patch.object(runner, 'run_role') as launch, self.assertRaises(s.Paused) as caught:
                    self.boundary()
                self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)
                self.assertEqual(1, len(self.state['planning']['recovery_review_grants']))
                self.assertFalse(self.state['planning']['recovery_review_grants'][0]['consumed'])
                launch.assert_not_called()
                source.unlink(missing_ok=True)
                Path(self.origins[0]['events']).write_bytes(events)

    def test_failed_recovery_cannot_mint_another_credit(self):
        self.origins[1]['timed_out'] = False
        self.boundary()
        record = self.charge()
        self.timeout(3, planning_recovery_grant=record['planning_recovery_grant'])
        with self.assertRaises(s.Paused) as caught:
            self.boundary()
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)
        self.assertEqual(1, len(self.state['planning']['recovery_review_grants']))

    def test_context_exposes_recovery_and_read_only_remediation(self):
        self.state['recovery_context'] = {'timeout_kind': 'idle', 'events': self.origins[0]['events']}
        self.boundary()
        prompt, _ = planning.context(self.state, 'astra_challenge', self.run / 'state.json')
        self.assertIn('resolver_remediation', prompt)
        self.assertIn('"timeout_kind": "idle"', prompt)
        self.assertIn('do not repeat exploratory tools', prompt)
        self.assertIn('exact final plan still requires human approval', prompt)

    def test_saved_grant_cannot_be_rebound_to_changed_route(self):
        self.boundary()
        self.state['settings']['roles']['astra']['model'] = 'different'
        grant = self.state['planning']['recovery_review_grants'][0]
        grant['binding']['settings_hash'] = s.digest(self.state['settings'])
        with self.assertRaises(s.Paused) as caught:
            self.charge()
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', caught.exception.status)

    def test_empty_missing_and_malformed_archive_evidence_denied(self):
        for origin in self.origins:
            Path(origin['before_ref']).unlink()
        self.assertFalse(self.boundary())

    def test_previous_cycle_discovery_cannot_fund_new_cycle(self):
        self.state['planning_history'] = [copy.deepcopy(self.state['planning'])]
        self.assertFalse(self.boundary())
        self.assertNotIn('recovery_review_grants', self.state['planning'])

    def test_ordinary_timeout_context_also_avoids_repeated_exploration(self):
        self.state['planning']['astra_calls'] = 1
        self.state['recovery_context'] = {'stage': 'astra_challenge', 'timeout_kind': 'idle'}
        self.assertFalse(self.boundary())
        prompt, _ = planning.context(self.state, 'astra_challenge', self.run / 'state.json')
        self.assertIn('resolver_remediation', prompt)
        self.assertIn('do not repeat exploratory tools', prompt)

    def test_diagnostic_receipts_preserve_budget_and_exhaustion_stages_human_request(self):
        recovery = {'stage': 'astra_challenge', 'events': self.origins[0]['events'], 'timeout_kind': 'idle'}
        before = copy.deepcopy(self.state)
        self.assertFalse(resolver.observe_operational_recovery(runner, self.state, self.run, self.root, recovery))
        self.assertEqual(before, self.state)
        self.state['automatic_timeout_recoveries'] = [recovery]
        self.assertTrue(resolver.observe_operational_recovery(runner, self.state, self.run, self.root, recovery))
        self.assertNotIn('recovery_review_grants', self.state['planning'])
        error = s.Paused('PAUSED_RESOLVER_OPERATIONAL', 'Exhausted')
        self.assertTrue(resolver.record_operational_exhaustion(runner, self.state, self.run, error))
        self.assertEqual('hold', self.state['stages'][-1]['decision']['action'])
        self.assertEqual('RESOLVER_PENDING', self.state['status'])
        self.assertIsNone(runner.resolver_human.current(self.state))
        self.assertEqual([], self.state['pending_questions'])
        runner.write_json(self.run / 'state.json', self.state)
        saved = s.read(self.run / 'state.json')
        self.assertEqual('WAITING_FOR_USER', saved['status'])
        public = runner.resolver_human.current(saved)
        self.assertEqual('operational_exhaustion', public['scope'])
        self.assertEqual('resolver', public['issuer'])
        self.assertEqual(before['goal_contract'], saved['goal_contract'])
        self.assertEqual(before['planning'], saved['planning'])
        self.assertEqual([recovery], saved['automatic_timeout_recoveries'])
        self.assertEqual(before['stages'], [row for row in saved['stages'] if not row.get('runner_owned')])
        self.assertFalse(resolver.record_operational_exhaustion(
            runner, self.state, self.run, s.Paused('PAUSED_PERMISSION', 'Human decides')))
