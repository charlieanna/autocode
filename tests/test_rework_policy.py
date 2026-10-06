"""Functional policy/seal tests; public CLI lifecycle coverage lives in the scenario gate."""
import copy
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import autocode_builder_policy as retry
import autocode as runtime
import autocode_rework_policy as policy
import autocode_support as support
import autocode_util as util
import autopilot


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

    def capture_case(self, *, shared, directory=None):
        case = self.case(seal=False)
        directory = directory or (self.workspace / '.autocode' / 'evidence' if shared else case.run / 'evidence')
        path = directory / (str(self.count) + '.json')
        command = [sys.executable, str(Path(runtime.__file__)), 'capture', '--output', str(path), '--no-compress',
                   '--', sys.executable, '-c', "import sys; print('invalid input reproduced'); sys.exit(1)"]
        env = {key: value for key, value in os.environ.items() if key != 'AUTOCODE_CAPTURE_CONTEXT'}
        # A ceiling, not a wait: under parallel suite load one capture CLI start can exceed 10 s (#506).
        captured = subprocess.run(command, cwd=self.workspace, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(1, captured.returncode, captured.stderr)
        receipt = json.loads(captured.stdout)
        self.assertEqual(receipt, util.read_object(path))
        report = util.read_object(case.accepted['output'])
        report['checks'] = [{'command': receipt['command_text'], 'exit_code': 1, 'evidence_ref': str(path)}]
        report['criterion_results'][0]['evidence_refs'] = [str(path)]
        report['findings'][0]['evidence'] = str(path)
        Path(case.accepted['output']).write_text(json.dumps(report))
        # The event envelope represents the actual capture command executed above.
        Path(case.accepted['events']).write_text(json.dumps({'type': 'item.completed', 'item': {
            'id': 'capture', 'type': 'tool_output', 'command': shlex.join(command),
            'aggregated_output': captured.stdout}}) + '\n')
        pins = support.evidence_hashes([str(path), receipt['full_output'], case.accepted['events']], self.workspace, case.run)
        case.state['validation'] = {**case.state['validation'], **copy.deepcopy(report), 'evidence_hashes': pins}
        support.verify_checks(copy.deepcopy(report['checks']), self.workspace, case.accepted['events'])
        policy.capture(case.accepted, report)
        policy.capture(case.record, case.decision)
        return case, path, Path(receipt['full_output'])

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
        for record in (case.record, case.accepted):
            for path, digest in record['rework_evidence']['hashes'].items():
                self.assertEqual(digest, receipt['evidence_hashes'][path])
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

    def test_supported_shared_capture_receipt_uses_normal_resolver_fallback(self):
        case, path, raw = self.capture_case(shared=True)
        seals = copy.deepcopy((case.record['rework_evidence'], case.accepted['rework_evidence']))
        self.assert_fallback(case)
        self.assertEqual('astra_resolve', case.state['next_stage'])
        case.runtime.lifecycle.assign_task.assert_not_called()
        self.assertIn(str(path), case.state['resolution_request']['evidence_hashes'])
        self.assertIn(str(raw), case.state['resolution_request']['evidence_hashes'])
        self.assertEqual(seals, (case.record['rework_evidence'], case.accepted['rework_evidence']))

    def test_current_own_run_capture_receipt_still_qualifies_for_direct_assignment(self):
        case, _, _ = self.capture_case(shared=False)
        self.assertTrue(self.route(case))
        self.assertEqual('assigned', case.state['current_task']['id'])
        self.assertEqual(1, len(case.state['builder_retry_decisions']))

    def test_shared_capture_artifact_tamper_missing_or_symlink_pauses(self):
        for label in ('receipt', 'output', 'missing-receipt', 'missing-output', 'symlink'):
            with self.subTest(label=label):
                case, path, raw = self.capture_case(shared=True)
                if label.startswith('missing'):
                    (path if label == 'missing-receipt' else raw).unlink()
                elif label == 'symlink':
                    replacement = case.run / 'replacement.log'
                    replacement.write_bytes(raw.read_bytes())
                    raw.unlink()
                    raw.symlink_to(replacement)
                else:
                    target = path if label == 'receipt' else raw
                    target.write_bytes(target.read_bytes() + b'\nchanged')
                with self.assertRaises(util.Paused):
                    self.route(case)
                case.queue.assert_not_called()
                case.runtime.lifecycle.assign_task.assert_not_called()

    def test_shared_layout_does_not_relax_stage_or_foreign_run_evidence_ownership(self):
        case, path, _ = self.capture_case(shared=True)
        case.record['events'] = str(path)
        with self.assertRaisesRegex(util.Paused, 'another run'):
            self.route(case)
        case, _, _ = self.capture_case(shared=True)
        foreign = self.workspace / '.autocode' / 'runs' / 'foreign' / 'receipt.json'
        foreign.parent.mkdir()
        foreign.write_text('{}')
        case.state['validation']['evidence_hashes'][str(foreign)] = util.file_hash(foreign)
        with self.assertRaisesRegex(util.Paused, 'another run'):
            self.route(case)

    def scratch(self):
        """Where tool containment tells a contained stage to capture (#419)."""
        return self.workspace / '.autocode' / ('tool-containment-' + uuid.uuid4().hex) / 'scratch'

    def contained_case(self, scratch):
        case, path, raw = self.capture_case(shared=False, directory=scratch)
        case.accepted['tool_containment'] = {'version': 1, 'workspace': str(self.workspace), 'scratch': str(scratch)}
        return case, path, raw

    def test_contained_validator_capture_in_its_own_scratch_reaches_the_resolver(self):
        case, path, raw = self.contained_case(self.scratch())
        seals = copy.deepcopy((case.record['rework_evidence'], case.accepted['rework_evidence']))
        self.assert_fallback(case)
        self.assertEqual('astra_resolve', case.state['next_stage'])
        case.runtime.lifecycle.assign_task.assert_not_called()
        self.assertIn(str(path), case.state['resolution_request']['evidence_hashes'])
        self.assertIn(str(raw), case.state['resolution_request']['evidence_hashes'])
        self.assertEqual(seals, (case.record['rework_evidence'], case.accepted['rework_evidence']))

    def test_containment_scratch_is_owned_only_through_the_accepted_validators_own_launch_record(self):
        case, _, _ = self.contained_case(self.scratch())
        self.assert_fallback(case)

        unrecorded, _, _ = self.capture_case(shared=False, directory=self.scratch())
        foreign_scratch = self.scratch()
        foreign, _, _ = self.capture_case(shared=False, directory=foreign_scratch)
        self.case().accepted['tool_containment'] = {'scratch': str(foreign_scratch)}
        sibling_scratch = self.scratch()
        sibling, _, _ = self.capture_case(shared=False, directory=sibling_scratch.with_name('scratch-copy'))
        sibling.accepted['tool_containment'] = {'scratch': str(sibling_scratch)}
        # This run recorded these too, but the accepted Validator's own launch did not.
        builder_scratch = self.scratch()
        builder, _, _ = self.capture_case(shared=False, directory=builder_scratch)
        builder.state['stages'][0]['tool_containment'] = {'scratch': str(builder_scratch)}
        earlier_scratch = self.scratch()
        earlier, _, _ = self.capture_case(shared=False, directory=earlier_scratch)
        earlier.state['stages'].insert(1, {'stage': 'sol', 'role': 'sol', 'rejected': True,
                                           'output': str(earlier.run / 'earlier.json'),
                                           'tool_containment': {'scratch': str(earlier_scratch)}})
        for label, case in (('unrecorded', unrecorded), ('recorded by another run', foreign),
                            ('beside the recorded scratch', sibling), ("recorded by this run's Builder", builder),
                            ('recorded by an earlier, rejected Validator attempt', earlier)):
            with self.subTest(label=label):
                with self.assertRaisesRegex(util.Paused, 'another run'):
                    self.route(case)
                case.queue.assert_not_called()

        for label in ('evidence file', 'scratch directory'):
            with self.subTest(label=label):
                case, _, raw = self.contained_case(self.scratch())
                elsewhere = case.run / 'elsewhere'
                if label == 'evidence file':
                    elsewhere.write_bytes(raw.read_bytes())
                    raw.unlink()
                    raw.symlink_to(elsewhere)
                else:
                    raw.parent.rename(elsewhere)
                    raw.parent.symlink_to(elsewhere)
                with self.assertRaisesRegex(util.Paused, 'symlink'):
                    self.route(case)
                case.queue.assert_not_called()

    def test_acceptance_refuses_the_contained_pins_rework_routing_would_refuse(self):
        # Live self-build 2026-10-06: route() refused accepted Validator pins only at REWORK, where the
        # Completion Reviewer cannot change them, so every retry of the review paused the same way.
        own = self.scratch()
        for label, (case, _, _) in (('own scratch', self.contained_case(own)),
                                    ('run directory', self.capture_case(shared=False)),
                                    ('shared evidence', self.capture_case(shared=True))):
            with self.subTest(label=label):
                policy.require_own_scratch(case.state['validation']['evidence_hashes'], case.accepted, self.workspace)
                self.route(case)  # assigned directly or queued for the Resolver, never paused
                case.queue.assert_called_once()
        for label, scratch in (("the Builder's scratch", self.scratch()), ('an unrecorded scratch', self.scratch())):
            with self.subTest(label=label):
                case, path, _ = self.capture_case(shared=False, directory=scratch)
                if label.startswith('the Builder'):
                    case.state['stages'][0]['tool_containment'] = {'scratch': str(scratch)}
                case.accepted['tool_containment'] = {'scratch': str(own)}
                with self.assertRaises(ValueError) as caught:
                    policy.require_own_scratch(case.state['validation']['evidence_hashes'], case.accepted, self.workspace)
                self.assertIn("another stage's tool containment: " + str(path), str(caught.exception))
                self.assertIn(str(own), str(caught.exception))
                with self.assertRaisesRegex(util.Paused, 'another run'):
                    self.route(case)
        # Runner-written workspace areas such as design captures are cited legitimately; acceptance leaves them alone.
        capture = self.workspace / '.autocode' / 'captures' / 'bundle' / 'manifest.json'
        capture.parent.mkdir(parents=True)
        capture.write_text('{}')
        policy.require_own_scratch({str(capture): util.file_hash(capture)}, {}, self.workspace)

    def test_a_validator_report_pinning_another_stages_scratch_is_rejected_when_accepted(self):
        # A ValueError from acceptance is the ordinary rejected-report path: a bounded report repair can
        # still drop or replace the citation, which no stage can do once a REWORK has started.
        scratch = self.scratch()
        case, path, _ = self.capture_case(shared=False, directory=scratch)
        case.state['stages'][0]['tool_containment'] = {'scratch': str(scratch)}
        report = util.read_object(case.accepted['output'])
        case.state['criteria_revision'] = util.digest(util.criteria_definition(case.state['acceptance_criteria']))
        state = copy.deepcopy(case.state)
        # Stop just after a report is accepted, before the fixture workspace would need a Git checkout.
        with patch.object(autopilot.findings_ledger, 'record_validation', side_effect=RuntimeError('accepted')):
            with self.assertRaisesRegex(ValueError, "another stage's tool containment: " + re.escape(str(path))):
                autopilot.apply_review_result(runtime, state, 'sol', copy.deepcopy(report), case.accepted,
                                              self.workspace, case.run)
            self.assertEqual(case.state, state)
            # The same report from the stage whose launch recorded that scratch gets past the ownership rule.
            case.accepted['tool_containment'] = {'scratch': str(scratch)}
            with self.assertRaisesRegex(RuntimeError, 'accepted'):
                autopilot.apply_review_result(runtime, state, 'sol', copy.deepcopy(report), case.accepted,
                                              self.workspace, case.run)
        self.assertIn(str(path), state['validation']['evidence_hashes'])

    def test_shared_receipt_requires_the_original_executed_capture_event(self):
        case, _, _ = self.capture_case(shared=True)
        # Prepare a distinct, internally sealed report without an execution event.
        events = Path(case.accepted['events'])
        events.write_text('')
        case.accepted.pop('rework_evidence')
        policy.capture(case.accepted, util.read_object(case.accepted['output']))
        case.state['validation']['evidence_hashes'][str(events)] = util.file_hash(events)
        with self.assertRaisesRegex(util.Paused, 'executed Validator receipt'):
            self.route(case)
        case.runtime.lifecycle.assign_task.assert_not_called()

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
        policy.capture(case.accepted, util.read_object(case.accepted['output']))
        self.assertTrue(self.route(case))
        self.assertEqual(before['hashes'][str(raw)],
                         case.state['direct_rework_assignments'][0]['evidence_hashes'][str(raw)])

    def test_verify_existing_never_captures_legacy_or_reads_repaired_artifacts(self):
        records = [{'output': '/not-owned', 'stage': 'sol'}]
        records += [{'output': '/not-owned', 'rework_evidence': {'version': 1}, flag: True}
                    for flag in ('report_only', 'report_repaired', 'repaired_by', 'applied_original_events')]
        with patch.object(util, 'read_object', side_effect=AssertionError('Unexpected legacy or repaired read')):
            for record in records:
                before = copy.deepcopy(record)
                self.assertIsNone(policy.verify_existing(record))
                self.assertEqual(before, record)

    def test_verify_existing_preserves_identity_and_rejects_lost_or_changed_metadata(self):
        case = self.case()
        before = copy.deepcopy(case.record)
        self.assertIsNone(policy.verify_existing(case.record))
        self.assertEqual(before, case.record)
        mutations = {
            'source': lambda record: record.update(source_revision='changed'),
            'task': lambda record: record.update(task_id='changed'),
            'contract': lambda record: record.update(contract_hash='changed'),
            'stage': lambda record: record.update(stage='terra'),
            'missing-events': lambda record: record.pop('events'),
            'missing-output': lambda record: record.pop('output'),
            'lost-source': lambda record: record.pop('source_revision'),
            'changed-root': lambda record: record['rework_evidence'].update(run_dir=str(case.run / 'other')),
            'empty-seal': lambda record: record.update(rework_evidence={}),
            'null-seal': lambda record: record.update(rework_evidence=None),
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(before)
                change(changed)
                mutated = copy.deepcopy(changed)
                with self.assertRaises(util.Paused):
                    policy.verify_existing(changed)
                self.assertEqual(mutated, changed)

    def test_load_stage_report_rejects_sealed_tamper_before_parser_or_output_writes(self):
        for field in ('output', 'events', 'reported_output'):
            for malformed in (False, True):
                with self.subTest(field=field, malformed=malformed):
                    case = self.case(seal=False)
                    raw = Path(case.record['output']).with_suffix('.reported.json')
                    raw.write_text('{}')
                    case.record.update(engine='opencode', reported_output=str(raw))
                    policy.capture(case.record, case.decision)
                    target = Path(case.record[field])
                    target.write_text('{malformed' if malformed else json.dumps({'status': 'COMPLETE'}))
                    before = copy.deepcopy(case.record)
                    original_bytes = {key: Path(case.record[key]).read_bytes()
                                      for key in ('output', 'events', 'reported_output')}
                    with patch.object(runtime.opencode, 'final_report', side_effect=ValueError('Parser must not run')) as parser:
                        with patch.object(runtime, 'write_json') as writer:
                            with self.assertRaises(util.Paused) as error:
                                runtime.load_stage_report(case.record, self.workspace, state=case.state)
                    self.assertEqual('PAUSED_STALE_HANDOFF', error.exception.status)
                    parser.assert_not_called()
                    writer.assert_not_called()
                    self.assertEqual(before, case.record)
                    self.assertEqual(original_bytes, {key: Path(case.record[key]).read_bytes() for key in original_bytes})

    def test_load_stage_report_unchanged_sealed_report_still_parses_without_repinning(self):
        case = self.case(seal=False)
        runtime.write_json(Path(case.record['output']), case.decision)
        schema = Path(case.record['output']).with_suffix('.schema.json')
        schema.write_text('{}')
        case.record.update(engine='opencode', schema=str(schema))
        policy.capture(case.record, case.decision)
        seal = copy.deepcopy(case.record['rework_evidence'])
        with patch.object(runtime.opencode, 'final_report', return_value=copy.deepcopy(case.decision)) as parser:
            with patch.object(runtime.support, 'review_validation_schema', return_value={}):
                with patch.object(runtime.support, 'validate_schema'):
                    with patch.object(runtime.support, 'hydrate_review_report', side_effect=lambda value, *args: value):
                        loaded = runtime.load_stage_report(case.record, self.workspace, state=case.state)
        self.assertEqual(case.decision, loaded)
        parser.assert_called_once()
        self.assertEqual(seal, case.record['rework_evidence'])

    def test_empty_existing_seal_cannot_be_downgraded_to_legacy(self):
        for seal in ({}, None):
            with self.subTest(seal=seal):
                case = self.case()
                case.record['rework_evidence'] = seal
                with patch.object(runtime.opencode, 'final_report') as parser, patch.object(runtime, 'write_json') as writer:
                    with self.assertRaises(util.Paused):
                        runtime.load_stage_report(case.record, self.workspace, state=case.state)
                parser.assert_not_called()
                writer.assert_not_called()
                with self.assertRaises(util.Paused):
                    self.route(case)
                with self.assertRaises(util.Paused):
                    policy.capture(case.record, {'status': 'REWORK'})
                case.record.pop('source_revision')
                with self.assertRaises(util.Paused):
                    policy.capture(case.record, case.decision)
                case.queue.assert_not_called()

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

    def test_queue_cannot_refresh_sealed_artifacts_changed_during_admission(self):
        for stage in ('completion', 'validator'):
            for field in ('output', 'events'):
                with self.subTest(stage=stage, field=field):
                    case = self.case()
                    seal = copy.deepcopy((case.record['rework_evidence'], case.accepted['rework_evidence']))

                    def changed(state, decision, record):
                        target = Path((record if stage == 'completion' else case.accepted)[field])
                        target.write_bytes(target.read_bytes() + b'\n')
                        self.queue(state, decision, record)

                    case.queue.side_effect = changed
                    with self.assertRaises(util.Paused):
                        self.route(case)
                    case.runtime.lifecycle.assign_task.assert_not_called()
                    self.assertEqual(seal, (case.record['rework_evidence'], case.accepted['rework_evidence']))

    def test_source_changed_by_queue_admission_pauses_before_assignment(self):
        case = self.case()

        def changed(state, decision, record):
            self.queue(state, decision, record)
            case.runtime.support.snapshot = lambda workspace: {'revision': 'changed'}

        case.queue.side_effect = changed
        with self.assertRaisesRegex(util.Paused, 'Source changed'):
            self.route(case)
        case.runtime.lifecycle.assign_task.assert_not_called()

    def test_public_apply_result_is_atomic_when_queue_changes_sealed_evidence(self):
        import autopilot
        case = self.case()
        before = copy.deepcopy(case.state)

        def changed(state, decision, record):
            self.queue(state, decision, record)
            events = Path(record['events'])
            events.write_bytes(events.read_bytes() + b'\n')

        with patch.object(runtime.goals, 'execution_guard'), \
             patch.object(runtime.planning, 'is_planning', return_value=False), \
             patch.object(runtime.workflow, 'enabled', return_value=False), \
             patch.object(autopilot.findings_ledger, 'record_decision'), \
             patch.object(autopilot, 'queue_resolution', side_effect=changed):
            with self.assertRaises(util.Paused):
                runtime.apply_result(case.state, 'astra_review', case.decision, case.record, self.workspace, case.run)
        self.assertEqual(before, case.state)

    def test_malformed_sealed_report_or_events_pause_without_paid_repair(self):
        for field in ('output', 'events'):
            with self.subTest(field=field):
                case = self.case()
                Path(case.accepted[field]).write_text('{malformed')
                with patch.object(runtime, 'run_role', side_effect=AssertionError('Paid repair must not run')) as paid:
                    with self.assertRaises(util.Paused) as error:
                        self.route(case)
                self.assertEqual('PAUSED_STALE_HANDOFF', error.exception.status)
                paid.assert_not_called()
                case.runtime.lifecycle.assign_task.assert_not_called()

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
