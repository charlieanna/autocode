"""Functional policy/seal tests; public CLI lifecycle coverage lives in the scenario gate."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import autocode_builder_policy as retry
import autocode_rework_policy as policy
import autocode_support as support
import autocode_util as util


class ReworkPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / 'workspace'
        self.workspace.mkdir()
        self.count = 0
        clock = patch.object(util, 'now', return_value='2026-10-03T00:00:00Z')
        clock.start()
        self.addCleanup(clock.stop)

    def case(self, *, state_change=None, decision_change=None, validation_change=None, seal=True, validator_source='source'):
        self.count += 1
        run = self.workspace / '.autocode' / 'runs' / str(self.count)
        directory = run / 'iterations' / '001'
        directory.mkdir(parents=True)
        common = {'contract_revision': 4, 'contract_hash': 'approved', 'task_id': 'current'}
        criteria = [{'id': 'C1', 'criterion': 'Reject invalid input', 'status': 'unverified', 'evidence': ''}]
        decision = {**common, 'status': 'REWORK', 'acceptance_criteria': copy.deepcopy(criteria),
            'next_objective': 'Restore input validation', 'affected_paths': ['greet.py'],
            'next_task': {'kind': 'implement', 'milestone_id': 'delivery',
                          'requirements': ['Reject invalid input'], 'acceptance_criteria': ['C1'],
                          'validation_plan': ['Execute the invalid-input regression'], 'findings': []},
            'findings': [{'id': 'F1', 'finding': 'Empty input is accepted', 'evidence': 'Validator event', 'blocking': True}],
            'evidence': ['Executed empty input exited 0 instead of 2'], 'user_request': {'kind': 'none'}}
        validator = {**common, 'verdict': 'FAIL',
            'checks': [{'command': 'check-invalid-input', 'exit_code': 1, 'evidence_ref': 'event:check'}],
            'criterion_results': [{'id': 'C1', 'status': 'FAIL', 'evidence_refs': ['event:check']}],
            'findings': [{'finding': 'Empty input is accepted', 'evidence': 'event:check'}]}
        if decision_change:
            decision_change(decision)
        if validation_change:
            validation_change(validator)

        def record(stage, value, role):
            output, events = directory / (stage + '.json'), directory / (stage + '.jsonl')
            output.write_text(json.dumps(value))
            events.write_text(json.dumps({'type': 'item.completed', 'item': {
                'id': 'check', 'type': 'command_execution', 'command': 'check-invalid-input', 'exit_code': 1}}) + '\n')
            result = {**common, 'stage': stage, 'role': role, 'output': str(output), 'events': str(events),
                      'source_revision': validator_source if stage == 'sol' else 'source', 'exit_code': 0, 'changed_files': [],
                      'launch_route': {'model': 'worker' if role == 'terra' else 'reviewer'},
                      'thread_id': role + '-session'}
            if seal and stage != 'terra':
                policy.capture(result, value)
            return result

        builder = record('terra', common, 'terra')
        accepted = record('sol', validator, 'sol')
        completion = record('astra_review', decision, 'completion')
        body = {'milestones': [{'id': 'delivery', 'affected_paths': ['greet.py'], 'acceptance_criteria': ['C1']}],
                'acceptance_criteria': [{'id': 'C1', 'criterion': 'Reject invalid input',
                                         'verification_method': 'Execute CLI', 'human_review': False}]}
        state = {'version': 3, 'workspace': str(self.workspace), 'iteration': 1,
            'status': 'RUNNING', 'phase': 'EXECUTING', 'next_stage': 'astra_review',
            'goal_contract': {'hash': 'approved', 'revision': 4, 'body': body, 'approval_status': 'approved'},
            'current_task': {**common, 'id': 'current', 'kind': 'implement', 'milestone_id': 'delivery',
                             'acceptance_criteria': ['C1'], 'affected_paths': ['greet.py']},
            'acceptance_criteria': copy.deepcopy(criteria), 'stages': [builder, accepted],
            'validation': {**copy.deepcopy(validator), 'output': accepted['output'], 'source_revision': validator_source,
                           'reviewer_role': 'sol', 'evidence_hashes': {accepted['events']: util.file_hash(accepted['events'])}},
            'settings': {'builder_retry': retry.configured(), 'orchestration': {'enabled': True, 'max_parallel': 2},
                         'roles': {'terra': {'model': 'worker', 'reasoning_effort': 'medium'},
                                   'sol': {'model': 'reviewer', 'reasoning_effort': 'high'}}},
            'sessions': {'terra': 'terra-session', 'sol': 'sol-session'}}
        if state_change:
            state_change(state)
        runtime = SimpleNamespace(
            support=SimpleNamespace(snapshot=lambda workspace: {'revision': 'source'}, verify_checks=support.verify_checks),
            lifecycle=SimpleNamespace(assign_task=Mock(side_effect=self.assign), wait_for_user=Mock()),
            goals=SimpleNamespace(record_decision=Mock()),
            milestones=SimpleNamespace(handle_gate=Mock(side_effect=self.gate)),
            dispatch=SimpleNamespace(build_stage=lambda state: 'orchestrator'),
            check_evidence_options=lambda record: {})
        return SimpleNamespace(state=state, decision=decision, record=completion, accepted=accepted,
                               runtime=runtime, run=run, queue=Mock(side_effect=self.queue))

    @staticmethod
    def queue(state, decision, record):
        contract, task = state['goal_contract'], state['current_task']
        if (contract['approval_status'] != 'approved' or decision['contract_hash'] != contract['hash']
                or decision['contract_revision'] != contract['revision'] or decision['task_id'] != task['id']
                or task['contract_hash'] != contract['hash'] or record['source_revision'] != 'source'):
            raise util.Paused('PAUSED_STALE_HANDOFF', 'Existing queue authority guard')
        state['acceptance_criteria'] = copy.deepcopy(decision['acceptance_criteria'])
        pins = {record['output']: util.file_hash(record['output'])}
        if record.get('events'):
            pins[record['events']] = util.file_hash(record['events'])
        validation = state['validation']
        if validation['source_revision'] == record['source_revision']:
            pins.update(validation['evidence_hashes'])
        state['resolution_request'] = {'source_output': record['output'], 'source_stage': 'astra_review',
            'contract_hash': contract['hash'], 'task_id': task['id'], 'source_revision': record['source_revision'],
            'review': copy.deepcopy(decision), 'provenance': 'completion_review_decision',
            'evidence_hashes': pins}
        state.update(status='RUNNING', phase='RESOLVING', next_stage='astra_resolve')

    @staticmethod
    def assign(state, decision, current):
        task = decision['next_task']
        state['current_task'] = {**copy.deepcopy(state['current_task']), **copy.deepcopy(task),
                                 'id': 'assigned', 'source_revision': current['revision']}
        return task['kind']

    @staticmethod
    def gate(state, error, current, **kwargs):
        state.update(status=error.status, phase='PAUSED_OR_BLOCKED', next_stage='astra_review')

    def route(self, case):
        return policy.route(case.runtime, case.state, case.decision, case.record, case.queue, retry, run_dir=case.run)

    def assert_fallback(self, case):
        expected = copy.deepcopy(case.state)
        self.queue(expected, case.decision, case.record)
        self.assertFalse(self.route(case))
        self.assertEqual(expected, case.state)
        case.queue.assert_called_once()
        case.runtime.goals.record_decision.assert_not_called()

    def test_first_serial_repair_uses_any_milestone_and_keeps_default_parallel_configuration(self):
        case = self.case()
        criteria = copy.deepcopy(case.state['acceptance_criteria'])
        routes = copy.deepcopy(case.state['settings']['roles'])
        self.assertTrue(self.route(case))
        case.queue.assert_called_once()
        self.assertEqual(2, case.state['iteration'])
        self.assertEqual('orchestrator', case.state['next_stage'])
        self.assertEqual('EXECUTING', case.state['phase'])
        self.assertEqual(criteria, case.state['acceptance_criteria'])
        self.assertEqual(routes, case.state['settings']['roles'])
        self.assertEqual('FAIL', case.state['validation']['verdict'])
        self.assertEqual(['retry'], [row['action'] for row in case.state['builder_retry_decisions']])
        self.assertEqual([case.record['output']], next(iter(case.state['builder_retries'].values()))['failures'])
        self.assertNotIn('resolution_request', case.state)
        self.assertNotIn('repair_plan', case.state)
        self.assertNotIn('resolution_history', case.state)
        receipt = case.state['direct_rework_assignments'][0]
        self.assertEqual(('current', 'assigned'), (receipt['source_task_id'], receipt['assigned_task_id']))
        self.assertEqual('completion_direct_assignment', receipt['provenance'])
        self.assertNotIn('diagnosis', receipt)
        case.runtime.goals.record_decision.assert_called_once()

    def test_incomplete_and_ambiguous_valid_decisions_keep_exact_resolver_fallback(self):
        mutations = {
            'no-task': lambda value: value.update(next_task={}),
            'validate-task': lambda value: value['next_task'].update(kind='validate'),
            'no-objective': lambda value: value.update(next_objective=' '),
            'no-requirements': lambda value: value['next_task'].update(requirements=[]),
            'no-plan': lambda value: value['next_task'].update(validation_plan=[]),
            'unknown-cid': lambda value: value['next_task'].update(acceptance_criteria=['C2']),
            'other-milestone': lambda value: value['next_task'].update(milestone_id='other'),
            'changed-status': lambda value: value['acceptance_criteria'][0].update(status='verified'),
            'partial-scope': lambda value: value.update(affected_paths=[]),
            'widened-scope': lambda value: value.update(affected_paths=['greet.py', 'outside.py']),
            'no-evidence': lambda value: value.update(evidence=[]),
            'no-defect': lambda value: value.update(findings=[]),
            'no-blocking-defect': lambda value: value['findings'][0].update(blocking=False),
            'permission': lambda value: value['user_request'].update(kind='permission'),
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                self.assert_fallback(self.case(decision_change=change))

    def test_parallel_progressive_recurrent_and_recovery_cases_fall_back(self):
        mutations = {
            'worker': lambda state: state.update(parent_run='parent'),
            'parallel-batch': lambda state: state.update(orchestration_batch={'workers': [{}, {}]}),
            'integrated-batch': lambda state: state['current_task'].update(milestone_ids=['delivery']),
            'progressive': lambda state: state.update(progressive={'version': 1}),
            'second-builder': lambda state: state['stages'].append(copy.deepcopy(state['stages'][0])),
            'diagnosed': lambda state: state.update(resolution_history=[{'source_revision': 'source'}]),
            'earlier-direct': lambda state: state.update(direct_rework_assignments=[{'source_output': 'earlier'}]),
            'timeout': lambda state: state.update(recovery_context={'timeout_kind': 'stage'}),
            'repair': lambda state: state['stages'].append({'stage': 'sol_report_repair', 'report_only': True}),
            'replan': lambda state: state.update(milestone_progress={'delivery': {'needs_replan': True}}),
            'disabled-retry': lambda state: state['settings']['builder_retry'].update(enabled=False),
            'zero-retry': lambda state: state['settings']['builder_retry'].update(ordinary_retries=0),
            'exhausted-retry': lambda state: state.update(builder_retries={'lane': {'failures': ['old'], 'action': 'pause'}}),
            'same-model': lambda state: state['stages'][0]['launch_route'].update(model='reviewer'),
            'same-model-alias': lambda state: state['stages'][0]['launch_route'].update(model='provider/reviewer'),
            'same-session': lambda state: state['stages'][0].update(thread_id='sol-session'),
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                self.assert_fallback(self.case(state_change=change))

    def test_pending_human_decision_is_preserved_before_real_queue_can_clear_it(self):
        import autopilot
        for key, value in (
            ('resolver_human_proposal', {'scope': 'permission'}),
            ('resolver_human_request', {'request_id': 'permission-1'}),
            ('pending_questions', [{'id': 'Q1'}]),
            ('user_request', {'kind': 'permission'}),
        ):
            with self.subTest(key=key):
                case = self.case(state_change=lambda state: state.update(status='WAITING_FOR_USER', **{key: value}))
                before = copy.deepcopy(case.state)
                case.queue = Mock(wraps=autopilot.queue_resolution)
                with patch.object(autopilot.goals, 'execution_guard'), \
                     patch.object(autopilot.support, 'snapshot', return_value={'revision': 'source'}):
                    with self.assertRaises(util.Paused) as raised:
                        self.route(case)
                self.assertEqual('PAUSED_RESOLVER', raised.exception.status)
                self.assertEqual(before, case.state)
                case.queue.assert_not_called()
                case.runtime.lifecycle.assign_task.assert_not_called()

    def test_historical_approval_display_is_not_an_unanswered_decision(self):
        # Legacy/domain-only approval can leave a display receipt. RUNNING plus
        # the approved contract is authoritative; real requests/questions still hold.
        case = self.case(state_change=lambda state: state.update(
            resolver_human_request={'scope': 'goal_approval'}, user_request={}))
        self.assertTrue(self.route(case))

    def test_valid_pass_and_non_integer_or_zero_failure_are_not_repair_authority(self):
        self.assert_fallback(self.case(validation_change=lambda value: value.update(verdict='PASS')))
        for exit_code in (None, False, True, 0, '1'):
            with self.subTest(exit_code=exit_code):
                self.assert_fallback(self.case(validation_change=lambda value: value['checks'][0].update(exit_code=exit_code)))

    def test_failed_check_requires_current_executed_event(self):
        case = self.case(validation_change=lambda value: value['checks'][0].update(command='unexecuted'))
        with self.assertRaisesRegex(util.Paused, 'executed Validator receipt'):
            self.route(case)
        self.assertNotIn('builder_retry_decisions', case.state)

    def test_runner_resolved_check_reference_matches_sealed_validator_report(self):
        case = self.case(validation_change=lambda value: value['criterion_results'][0].update(
            evidence_refs=['check:1']))
        # The existing Validator acceptance path resolves this runner-owned alias.
        case.state['validation']['criterion_results'][0]['evidence_refs'] = ['event:check']
        original = Path(case.accepted['output']).read_bytes()
        self.assertTrue(self.route(case))
        self.assertEqual(original, Path(case.accepted['output']).read_bytes())

    def test_nonpassing_status_updates_do_not_change_criterion_identity(self):
        for status in ('blocked', 'unverified'):
            with self.subTest(status=status):
                case = self.case(decision_change=lambda value: value['acceptance_criteria'][0].update(status=status))
                self.assertTrue(self.route(case))
                self.assertEqual(status, case.state['acceptance_criteria'][0]['status'])

    def test_missing_event_pin_falls_back_without_replacing_original_pins(self):
        self.assert_fallback(self.case(state_change=lambda state: state['validation'].update(evidence_hashes={})))

    def test_old_persisted_reports_without_seals_fall_back(self):
        case = self.case(seal=False)
        self.assert_fallback(case)
        self.assertNotIn('rework_evidence', case.record)
        self.assertNotIn('rework_evidence', case.accepted)

    def test_legacy_custom_run_and_minimal_stage_record_keep_original_queue_behavior(self):
        case = self.case(seal=False)
        case.run = self.workspace / 'legacy-run'
        case.run.mkdir()
        output = case.run / 'review.json'
        output.write_text(json.dumps(case.decision))
        case.record = {'output': str(output), 'source_revision': 'source'}
        self.assert_fallback(case)

    def test_unchanged_historical_validator_keeps_resolver_fallback(self):
        case = self.case(validator_source='previous-source')
        source_file = self.workspace / 'greet.py'
        source_file.write_text('new source')
        case.state['validation']['evidence_hashes'][str(source_file)] = 'superseded-old-source-hash'
        self.assert_fallback(case)
        self.assertNotIn(str(source_file), case.state['resolution_request']['evidence_hashes'])

    def test_repaired_validator_is_not_promoted_to_a_fresh_unrepaired_seal(self):
        case = self.case()
        case.accepted.update(stage='sol_report_repair', report_only=True)
        case.accepted.pop('rework_evidence')
        self.assertIsNotNone(policy.capture(case.accepted, case.state['validation']))
        self.assertNotIn('rework_evidence', case.accepted)
        self.assert_fallback(case)

    def test_repaired_original_completion_keeps_resolver_fallback_without_repinning(self):
        case = self.case()
        original_seal = copy.deepcopy(case.record['rework_evidence'])
        repaired = Path(case.record['output']).with_name('completion-report-repair.json')
        repaired.write_text(json.dumps(case.decision))
        case.record.update(output=str(repaired), report_repaired=True, repaired_by='repair-events')
        self.assertIs(case.decision, policy.capture(case.record, case.decision))
        self.assertEqual(original_seal, case.record['rework_evidence'])
        self.assert_fallback(case)

    def test_repaired_original_validator_keeps_fallback_without_trusting_old_seal(self):
        case = self.case()
        report = util.read_object(case.accepted['output'])
        repaired = Path(case.accepted['output']).with_name('validator-report-repair.json')
        repaired.write_text(json.dumps(report))
        case.accepted.update(output=str(repaired), report_repaired=True)
        case.state['validation']['output'] = str(repaired)
        self.assert_fallback(case)

    def test_capture_skips_legacy_and_unrelated_stages(self):
        for stage in ('terra', 'astra_checkpoint', 'astra_review_report_repair'):
            with self.subTest(stage=stage):
                case = self.case(seal=False)
                case.record['stage'] = stage
                policy.capture(case.record, case.decision)
                self.assertNotIn('rework_evidence', case.record)
        case = self.case(seal=False)
        legacy = {'status': 'REWORK'}
        policy.capture(case.record, legacy)
        self.assertNotIn('rework_evidence', case.record)

    def test_capture_is_idempotent_and_includes_reported_output(self):
        case = self.case(seal=False)
        raw = Path(case.record['output']).with_suffix('.reported.json')
        raw.write_text(json.dumps({'raw': 'retained'}))
        case.record['reported_output'] = str(raw)
        self.assertIs(case.decision, policy.capture(case.record, case.decision))
        before = copy.deepcopy(case.record['rework_evidence'])
        self.assertIn(str(raw), before['hashes'])
        self.assertIs(case.decision, policy.capture(case.record, case.decision))
        self.assertEqual(before, case.record['rework_evidence'])

    def test_capture_does_not_refresh_changed_existing_seal(self):
        for field in ('output', 'events', 'reported_output'):
            with self.subTest(field=field):
                case = self.case(seal=False)
                raw = Path(case.record['output']).with_suffix('.reported.json')
                raw.write_text('{}')
                case.record['reported_output'] = str(raw)
                policy.capture(case.record, case.decision)
                seal = copy.deepcopy(case.record['rework_evidence'])
                path = Path(case.record[field])
                path.write_bytes(path.read_bytes() + b'\n')
                with self.assertRaisesRegex(util.Paused, 'repinned'):
                    policy.capture(case.record, case.decision)
                self.assertEqual(seal, case.record['rework_evidence'])

    def test_existing_seal_does_not_become_legacy_when_provenance_is_removed(self):
        case = self.case()
        with self.assertRaisesRegex(util.Paused, 'accepted provenance'):
            policy.capture(case.record, {'status': 'REWORK'})
        case.record.pop('source_revision')
        with self.assertRaisesRegex(util.Paused, 'runner provenance'):
            policy.capture(case.record, case.decision)

    def test_unreadable_sealed_file_pauses_instead_of_triggering_report_repair(self):
        case = self.case()
        with patch.object(util, 'file_hash', side_effect=PermissionError('unreadable')):
            with self.assertRaisesRegex(util.Paused, 'cannot be read'):
                self.route(case)

    def test_current_value_must_match_saved_normalized_report(self):
        case = self.case()
        case.decision['next_objective'] = 'Different objective'
        with self.assertRaisesRegex(util.Paused, 'accepted value'):
            self.route(case)

    def test_stale_authority_and_validator_semantic_bindings_pause(self):
        mutations = {
            'unapproved': lambda case: case.state['goal_contract'].update(approval_status='draft'),
            'stale-contract': lambda case: case.state['current_task'].update(contract_hash='stale'),
            'stale-source': lambda case: case.record.update(source_revision='stale'),
            'changed-validation-body': lambda case: case.state['validation'].update(verdict='PASS'),
            'stale-validation-source': lambda case: case.state['validation'].update(source_revision='stale'),
            'stale-validation-task': lambda case: case.state['validation'].update(task_id='stale'),
            'stale-seal': lambda case: case.record['rework_evidence'].update(report_digest='forged'),
            'stale-validator-pin': lambda case: case.state['validation']['evidence_hashes'].update({case.accepted['events']: 'stale'}),
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                case = self.case()
                change(case)
                with self.assertRaises(util.Paused):
                    self.route(case)
                self.assertNotIn('builder_retry_decisions', case.state)

    def test_stage_or_evidence_files_in_another_run_are_not_read(self):
        case, other = self.case(), self.case()
        case.record['events'] = other.record['events']
        with patch.object(util, 'file_hash', side_effect=AssertionError('Off-run evidence was read')):
            with self.assertRaisesRegex(util.Paused, 'another run'):
                self.route(case)
        case = self.case()
        case.state['validation']['evidence_hashes'][other.accepted['events']] = 'forged'
        with self.assertRaisesRegex(util.Paused, 'another run'):
            self.route(case)

    def test_report_cannot_supply_its_own_foreign_run_authority(self):
        case, other = self.case(), self.case()
        with self.assertRaisesRegex(util.Paused, 'another run'):
            policy.route(case.runtime, case.state, other.decision, other.record, case.queue, retry, run_dir=case.run)

    def test_symlinked_stage_evidence_is_rejected_before_read(self):
        case, other = self.case(), self.case()
        events = Path(case.record['events'])
        events.unlink()
        events.symlink_to(other.record['events'])
        with self.assertRaisesRegex(util.Paused, 'symlink'):
            self.route(case)

    def test_assignment_format_failure_does_not_assign_or_charge_before_fallback(self):
        case = self.case()

        def invalid(state, decision, current):
            state['current_task'] = {'id': 'unaccepted'}
            raise ValueError('Task needs more validation detail')

        case.runtime.lifecycle.assign_task.side_effect = invalid
        self.assert_fallback(case)
        self.assertEqual('current', case.state['current_task']['id'])

    def test_additional_configured_ordinary_retries_still_charge_one_unused_attempt(self):
        case = self.case(state_change=lambda state: state['settings']['builder_retry'].update(ordinary_retries=2))
        self.assertTrue(self.route(case))
        self.assertEqual(1, case.state['builder_retry_decisions'][0]['attempt'])

    def test_dispatch_or_assignment_authority_failure_does_not_commit_an_unaccepted_task(self):
        case = self.case()
        original = copy.deepcopy(case.state['current_task'])
        case.runtime.dispatch.build_stage = Mock(side_effect=util.Paused('PAUSED_CROSS_MODEL', 'Invalid verifier'))
        with self.assertRaisesRegex(util.Paused, 'Invalid verifier'):
            self.route(case)
        self.assertEqual(original, case.state['current_task'])
        self.assertNotIn('builder_retry_decisions', case.state)

    def test_milestone_pause_uses_existing_gate_and_preserves_one_retry_charge(self):
        case = self.case()
        case.runtime.lifecycle.assign_task.side_effect = util.Paused('PAUSED_MILESTONE_BUDGET', 'Budget exhausted')
        self.assertTrue(self.route(case))
        self.assertEqual('PAUSED_MILESTONE_BUDGET', case.state['status'])
        self.assertEqual('astra_review', case.state['next_stage'])
        self.assertEqual('current', case.state['current_task']['id'])
        self.assertIn('resolution_request', case.state)
        self.assertNotIn('direct_rework_assignments', case.state)
        self.assertEqual(1, len(case.state['builder_retry_decisions']))
        case.runtime.milestones.handle_gate.assert_called_once()
        case.runtime.goals.record_decision.assert_called_once()

    def test_non_milestone_pause_is_not_swallowed(self):
        case = self.case()
        case.runtime.lifecycle.assign_task.side_effect = util.Paused('PAUSED_STALE_HANDOFF', 'Changed authority')
        with self.assertRaisesRegex(util.Paused, 'Changed authority'):
            self.route(case)
        self.assertNotIn('builder_retry_decisions', case.state)

    def test_replaying_an_assigned_report_cannot_charge_twice(self):
        case = self.case()
        self.assertTrue(self.route(case))
        decision_count = case.runtime.goals.record_decision.call_count
        charged = copy.deepcopy(case.state['builder_retry_decisions'])
        with self.assertRaises(util.Paused):
            self.route(case)
        self.assertEqual(charged, case.state['builder_retry_decisions'])
        self.assertEqual(decision_count, case.runtime.goals.record_decision.call_count)
        self.assertEqual(1, len(case.state['direct_rework_assignments']))


if __name__ == '__main__':
    unittest.main()
