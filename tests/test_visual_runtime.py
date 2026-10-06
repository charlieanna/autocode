"""Offline boundary tests: real files/capture verification, TEST-ONLY delivery.

Parent snapshots are explicit functional inputs computed from the owned fixture
files. No Git mutation, browser, HTTP provider or model invocation occurs here.
These controls qualify the bridge, not native transport or visual judgment.
"""
from copy import deepcopy
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import autocode_contract_identity as contract
import autocode_goals as goals
import autocode_rework_policy as reports
import autocode_util as util
import autocode_visual_evidence as evidence
import autocode_visual_runtime as visual
import autocode
import autocode_status_command
import autocode_taskrun as taskrun
import goal_fixtures
from tests.visual_capture_fixtures import make_capture, png


class VisualRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='visual-runtime-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / '.autocode' / 'runs' / 'test'
        self.run.mkdir(parents=True)
        (self.root / 'greet.py').write_text('print("rendered implementation")\n')
        exports = self.root / '.autocode' / 'design-inputs' / 'reference'
        exports.mkdir(parents=True)
        cases = []
        for index, name in enumerate(('empty', 'filled')):
            image, context = exports / (name + '.png'), exports / (name + '.json')
            png(image, 2, 1)
            context.write_text(json.dumps({'state': name}))
            cases.append({'id': name, 'file_key': 'Fixture', 'node_id': '1:2', 'state': name,
                          'route': '/' + name, 'implementation_paths': ['greet.py'],
                          'viewport': {'width': 2, 'height': 1, 'device_scale_factor': 1}, 'export_scale': 1,
                          'artifacts': {kind: {'path': path.name, 'sha256': util.file_hash(path)}
                                        for kind, path in (('screenshot', image), ('design_context', context))}})
        body = {'version': 1, 'files': [{'key': 'Fixture', 'nodes': ['1:2']}], 'cases': cases}
        criteria = [{'id': cid, 'criterion': name + ' rendered state matches the approved design',
                     'verification_method': 'Independent image review and real functional tests', 'human_review': False}
                    for cid, name in (('C1', 'empty'), ('C2', 'filled'))]
        approved_body = goal_fixtures.body()
        approved_body['acceptance_criteria'] = criteria
        approved_body['milestones'][0]['acceptance_criteria'] = ['C1', 'C2']
        approved_body['constraints'].append('VISUAL_CASE_CRITERIA={"empty":["C1"],"filled":["C2"]}')
        util.validate_schema(approved_body, goals.BODY_SCHEMA)
        self.state = {'workspace': str(self.root), 'settings': {
            'design_manifest': {'root': str(exports), 'body': body, 'manifest_hash': util.digest(body)},
            'roles': {'sol': {'engine': 'opencode', 'provider': None, 'model': 'openai/reviewer'}}},
            'current_task': {'id': 'T1'}, 'acceptance_criteria': deepcopy(criteria), 'stages': [],
            'goal_contract': {'task_id': 'job', 'revision': 1, 'approval_status': 'approved',
                              'body': approved_body}}
        self.approve()
        with patch.object(util, 'snapshot', side_effect=lambda root: self.snapshot()):
            self.captures = {case['id']: make_capture(self.root, util.digest(body), case) for case in cases}
        self.builder = {'stage': 'terra', 'role': 'terra', 'engine': 'opencode', 'task_id': 'T1',
                        'exit_code': 0, 'source_revision': self.snapshot()['revision'], 'thread_id': 'builder-session',
                        'launch_route': {'engine': 'opencode', 'model': 'fixture/builder'},
                         'events': str(self.run / 'builder.jsonl'), 'output': str(self.run / 'builder.json')}
        self.builder['after_ref'] = str(self.run / 'builder.after.json')
        util.atomic_json(Path(self.builder['after_ref']), self.snapshot())
        Path(self.builder['events']).write_text(json.dumps({'type': 'step_finish', 'sessionID': 'builder-session'}) + '\n')
        util.atomic_json(Path(self.builder['output']), {'status': 'BUILT_NOT_VISUALLY_ACCEPTED'})
        self.state['stages'].append(self.builder)
        self.command = ['opencode', 'run', '--model', 'openai/reviewer', '--agent', 'autocode_sol']
        self.env = {'OPENCODE_CONFIG_CONTENT': json.dumps({'permission': {'read': 'deny', 'bash': {'*': 'deny'}}}),
                    'HOME': '/original/home', 'SOME_AUTH_ENV': 'fixture-unchanged-never-written'}
        self.base = self.run / 'sol'
        self.proof = self.run / 'qualified-config.json'
        self.proof.write_text('{"test_only":true}')
        self.limits = {'width': 2, 'height': 1, 'pixels': 2, 'bytes': 4096}
        self.request_limit = visual.delivery.SESSION_MAX_REQUESTS

    def snapshot(self):
        value = {'head': 'fixture-parent-snapshot', 'files': {'greet.py': util.file_hash(self.root / 'greet.py')}}
        return {**value, 'revision': util.digest(value)}

    def approve(self):
        saved = self.state['goal_contract']
        saved['hash'] = util.digest({key: saved[key] for key in ('task_id', 'revision', 'body')})
        saved['approval_event'] = {'actor': 'user_cli', 'token': contract.token(saved)}
        self.state['user_events'] = [deepcopy(saved['approval_event'])]

    def qualified_config(self, **kwargs):
        # TEST ONLY. Production must recheck actual native qualification evidence.
        self.assertEqual(self.command, kwargs['command'][:len(self.command)])
        self.assertEqual('fixture-unchanged-never-written', kwargs['env']['SOME_AUTH_ENV'])
        environment = dict(kwargs['env'])
        audit = json.loads(environment['AUTOCODE_IMAGE_AUDIT'])
        self.assertNotIn('max_requests', audit)
        audit['max_requests'] = self.request_limit
        environment['AUTOCODE_IMAGE_AUDIT'] = json.dumps(audit)
        return {'evidence_hashes': {str(self.proof): util.file_hash(self.proof)}, 'image_limits': self.limits,
                'environment': environment}

    def prepare(self, **options):
        options = {'launch_authority': self.qualified_config, 'current_snapshot': self.snapshot(), **options}
        return visual.prepare(self.state, 'sol', self.root, self.run, self.base,
                               self.command, self.env, 'Validate the implementation', **options)

    def test_authorized_environment_is_used_and_bound_without_persisting_secrets(self):
        def qualified(**kwargs):
            result = self.qualified_config(**kwargs)
            result['environment'] = {**result['environment'], 'HOME': str(self.run / 'qualified-home')}
            return result
        context, command, environment, _ = self.prepare(launch_authority=qualified)
        self.assertEqual('READY', context['status'])
        self.assertEqual(str(self.run / 'qualified-home'), environment['HOME'])
        self.assertEqual(visual.delivery.SESSION_MAX_REQUESTS, context['audit_options']['max_requests'])
        self.assertEqual('autocode_sol', context['audit_options']['reviewer']['agent'])
        self.assertNotIn('fixture-unchanged-never-written', Path(context['launch_manifest']).read_text())
        visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(),
                                command=command, env=environment)
        with self.assertRaises(util.Paused):
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(),
                                    command=command, env={**environment, 'HOME': '/unapproved'})

    def test_authority_supplies_its_approved_session_cap_without_runtime_escalation(self):
        for cap in (1, 4, visual.delivery.SESSION_MAX_REQUESTS):
            with self.subTest(cap=cap):
                self.request_limit = cap
                context, command, environment, _ = self.prepare()
                self.assertEqual('READY', context['status'], context)
                self.assertEqual(cap, context['audit_options']['max_requests'])
                visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(),
                                        command=command, env=environment)
        for cap in (None, False, 0, visual.delivery.SESSION_MAX_REQUESTS + 1):
            with self.subTest(cap=cap):
                self.request_limit = cap
                context, command, environment, prompt = self.prepare()
                self.assertEqual('NOT_READY', context['status'], context)
                self.assertEqual((self.command, self.env, 'Validate the implementation'), (command, environment, prompt))
                self.assertIn('approved bounded session request cap', context['reason'])

    def test_qualified_child_executable_is_rechecked_before_launch(self):
        executable = self.run / 'qualified-opencode'
        executable.write_text('qualified executable bytes')
        def qualified(**kwargs):
            result = self.qualified_config(**kwargs)
            result['child_identity'] = {'executable': str(executable), 'executable_sha256': util.file_hash(executable),
                                       'command_sha256': util.digest(kwargs['command']),
                                       'environment_sha256': util.digest(result['environment'])}
            return result
        context, command, env, _ = self.prepare(launch_authority=qualified)
        with patch.object(visual.shutil, 'which', return_value=str(executable)):
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)
            executable.write_text('changed executable bytes')
            with self.assertRaises(util.Paused):
                visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)

    def review(self, *, statuses=('PASS', 'PASS')):
        context, command, env, _ = self.prepare()
        self.assertEqual('READY', context['status'], context)
        visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)
        self.record = {'stage': 'sol', 'role': 'sol', 'engine': 'opencode', 'exit_code': 0,
                       'task_id': 'T1', 'contract_revision': 1, 'contract_hash': self.state['goal_contract']['hash'],
                       'source_revision': self.snapshot()['revision'], 'expected_session': None, 'thread_id': 'reviewer-session',
                       'launch_route': deepcopy(self.state['settings']['roles']['sol']),
                       'events': str(self.run / 'sol.jsonl'), 'output': str(self.run / 'sol.json'),
                       'before_ref': str(self.run / 'sol.before.json'), 'after_ref': str(self.run / 'sol.after.json'),
                       'finished_at': '2026-10-04T03:00:00+00:00', 'visual_runtime': context}
        self.report = {key: self.record[key] for key in ('task_id', 'contract_revision', 'contract_hash')}
        self.report.update(verdict='PASS', design_manifest_hash=self.state['settings']['design_manifest']['manifest_hash'],
                           design_results=[{'id': cid, 'status': status,
                                            'criterion_ids': context['current']['case_criteria'][cid], **self.captures[cid]}
                                           for cid, status in zip(('empty', 'filled'), statuses)])
        self.save_report()
        for key in ('before_ref', 'after_ref'):
            util.atomic_json(Path(self.record[key]), self.snapshot())
        reports.capture(self.record, self.report)
        self.validation = {**deepcopy(self.report), 'source_revision': self.record['source_revision'],
                           'output': self.record['output'], 'evidence_hashes': {self.record['events']: util.file_hash(self.record['events'])}}
        self.state['stages'].append(self.record)
        # This is deliberately not a real audit; only the test callback reads it.
        Path(context['audit_path']).write_text('{"test_only_delivery":true}\n')

    def save_report(self, *, reason='stop'):
        util.atomic_json(Path(self.record['output']), self.report)
        rows = [{'type': kind, 'sessionID': 'reviewer-session',
                 'part': {'id': kind, 'messageID': 'message', 'sessionID': 'reviewer-session', **fields}}
                for kind, fields in [('step_start', {}), ('text', {'text': json.dumps(self.report)}),
                                     ('step_finish', {'reason': reason})]]
        Path(self.record['events']).write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def fake_delivery(self, record, *, events, report, images, binding, audit_path, attempt_id, plugin_sha256):
        # TEST ONLY, never installed through state/config or a production hook.
        context = record['visual_runtime']
        self.assertEqual((context['audit_path'], context['attempt_id'], context['plugin_sha256']),
                         (audit_path, attempt_id, plugin_sha256))
        return {'version': 1, 'kind': 'final_request_image_delivery', 'image_capable': True, 'completed': True,
                'after_final_transform': True, 'session_id': 'reviewer-session', 'message_id': 'message',
                'finish_id': 'step_finish', **binding['reviewer'], 'binding_sha256': util.digest(binding),
                'events_sha256': hashlib.sha256(events).hexdigest(), 'report_sha256': hashlib.sha256(report).hexdigest(),
                'request_id': 'test-request', 'response_id': 'test-response', 'images': images,
                'evidence_ref': audit_path, 'evidence_sha256': util.file_hash(audit_path)}

    def accept(self, *, delivery=None, validation=None):
        with patch.object(visual.delivery, 'verify', side_effect=delivery or self.fake_delivery):
            return visual.accept_review(None, self.state, self.record, run_dir=self.run,
                                        current_snapshot=self.snapshot(), accepted_validation=self.validation if validation is None else validation)

    def assert_blocked(self, operation):
        before = deepcopy(self.state.get('visual_acceptance_receipts'))
        with self.assertRaises(util.Paused) as error:
            operation()
        self.assertEqual('PAUSED_VISUAL_EVIDENCE', error.exception.status)
        self.assertEqual(before, self.state.get('visual_acceptance_receipts'))

    def test_preparation_stages_exact_images_preserves_env_permissions_and_state(self):
        before = deepcopy(self.state)
        context, command, env, prompt = self.prepare()
        self.assertEqual('READY', context['status'], context)
        self.assertEqual(before, self.state)
        self.assertEqual(4, command.count('--file'))
        self.assertEqual(self.env['HOME'], env['HOME'])
        self.assertEqual(json.loads(self.env['OPENCODE_CONFIG_CONTENT'])['permission'], json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission'])
        self.assertNotIn('fixture-unchanged-never-written', Path(context['launch_manifest']).read_text())
        for image in context['images']:
            self.assertEqual(image['sha256'], util.file_hash(image['path']))
            self.assertEqual(0, Path(image['path']).stat().st_mode & 0o222)
        self.assertIn('Inspect BOTH images', prompt)

    def test_capture_wrapper_passes_full_snapshot_and_all_cases(self):
        self.review()
        with patch.object(evidence, 'verify', wraps=evidence.verify) as verify:
            receipt = self.accept()
        self.assertEqual('PASS', receipt['verdict'])
        self.assertTrue(all(call.kwargs['current'] == self.snapshot() for call in verify.call_args_list))
        self.assertEqual({'empty', 'filled'}, {call.kwargs['case']['id'] for call in verify.call_args_list})
        self.assertEqual(receipt, self.accept())
        self.assertEqual(1, len(self.state['visual_acceptance_receipts']))

    def operator_source(self):
        initial = {'kind': 'validate', 'objective': 'Validate the operator-provided rendered states',
                   'affected_paths': ['greet.py'], 'milestone_id': 'M1',
                   'requirements': list(self.state['goal_contract']['body']['required_behaviors']),
                   'acceptance_criteria': ['C1', 'C2'], 'validation_plan': ['Inspect both rendered states']}
        self.state['goal_contract']['body']['initial_task'] = initial
        util.validate_schema(self.state['goal_contract']['body'], goals.PLANNING_BODY_SCHEMA)
        self.state['current_task'] = {**deepcopy(initial), 'id': 'T1', 'criterion_ids': ['C1', 'C2']}
        self.state['stages'] = []
        self.approve()

    def test_operator_supplied_validation_does_not_fabricate_a_builder(self):
        self.operator_source()
        self.review()
        receipt = self.accept()
        self.assertEqual('operator_supplied_source', receipt['reviewer']['producer_kind'])
        self.assertIsNone(receipt['reviewer']['producer_model'])
        self.assertIsNone(receipt['reviewer']['producer_session_id'])
        source = receipt['reviewer']['operator_source']
        self.assertEqual(self.snapshot()['revision'], source['source_revision'])
        self.assertEqual(util.digest(self.state['goal_contract']['approval_event']), source['approval_event_sha256'])
        self.assertTrue(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))

    def test_operator_source_requires_approved_validation_and_no_hidden_writer(self):
        self.operator_source()
        original = deepcopy(self.state)
        changes = [
            lambda: self.state['current_task'].update(kind='implement'),
            lambda: self.state['current_task'].update(criterion_ids=['C1']),
            lambda: self.state['stages'].append({**self.builder, 'task_id': 'earlier-task', 'rejected': True}),
            lambda: self.state.update(parent_run='some-parent'),
            lambda: self.state['goal_contract'].update(approval_status='draft'),
        ]
        for change in changes:
            self.state = deepcopy(original)
            change()
            self.assertEqual('NOT_READY', self.prepare()[0]['status'])

    def test_supplied_source_provenance_cannot_be_relabelled_after_preparation(self):
        self.operator_source()
        context, command, env, _ = self.prepare()
        self.assertEqual('READY', context['status'])
        self.state['stages'].append({**self.builder, 'task_id': 'earlier-task'})
        with self.assertRaises(util.Paused):
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)

    def test_validation_task_carries_exact_post_write_producer_not_its_task_id(self):
        self.state['current_task'].update(id='T2', kind='validate')
        context, _, _, _ = self.prepare()
        self.assertEqual('READY', context['status'])
        self.assertEqual('T1', context['producer']['producer_task_id'])
        self.assertEqual(self.snapshot()['revision'], context['producer']['source_revision'])
        self.builder['source_revision'] = 'builder-launch-revision'
        self.assertEqual('NOT_READY', self.prepare()[0]['status'])

    def test_changed_producer_post_write_evidence_cannot_be_reused(self):
        context, command, env, _ = self.prepare()
        self.assertEqual('READY', context['status'])
        util.atomic_json(Path(self.builder['after_ref']), {'revision': 'other', 'files': {}})
        with self.assertRaises(util.Paused):
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)

    def test_current_projection_and_completion_are_read_only(self):
        self.review()
        self.accept()
        before = deepcopy(self.state)
        result = visual.projection(self.state, current_snapshot=self.snapshot())
        self.assertTrue(result['current_all_accepted'], result)
        self.assertEqual((2, 1), (result['accepted_cases'], result['accepted_frames']))
        self.assertTrue(visual.completion_check(self.state, current_snapshot=self.snapshot())['passed'])
        self.assertEqual(before, self.state)

    def test_opted_in_completion_requires_current_visual_authority(self):
        self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
        with self.assertRaises(util.Paused):
            visual.require_completion(self.state, current_snapshot=self.snapshot())
        self.review()
        self.accept()
        self.assertTrue(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
        visual.require_completion(self.state, current_snapshot=self.snapshot())
        Path(self.record['visual_runtime']['audit_path']).unlink()
        self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
        with self.assertRaises(util.Paused):
            visual.require_completion(self.state, current_snapshot=self.snapshot())
        self.assertTrue(visual.completion_allowed({}, current_snapshot={}))

    def test_mixed_and_unverified_verdicts_never_complete(self):
        self.review(statuses=('PASS', 'NOT_VERIFIED'))
        self.accept()
        result = visual.projection(self.state, current_snapshot=self.snapshot())
        self.assertFalse(result['current_all_accepted'])
        self.assertEqual(1, result['accepted_cases'])
        self.assertFalse(result['coverage_complete'])

    def test_missing_config_authority_or_captures_never_prepares(self):
        files = sorted(self.run.rglob('*'))
        context, command, env, prompt = self.prepare(launch_authority=None)
        self.assertEqual('NOT_READY', context['status'])
        self.assertIn('bootstrap authority', context['reason'])
        self.assertEqual((self.command, self.env, 'Validate the implementation'), (command, env, prompt))
        self.assertEqual(files, sorted(self.run.rglob('*')))
        for row in self.captures.values():
            Path(row['capture_ref']).unlink()
        context, *_ = self.prepare()
        self.assertIn('Missing CURRENT captures', context['reason'])

    def test_missing_one_case_and_stale_source_do_not_silently_select_partial_coverage(self):
        Path(self.captures['filled']['capture_ref']).unlink()
        self.assertEqual('NOT_READY', self.prepare()[0]['status'])
        (self.root / 'greet.py').write_text('changed source\n')
        self.assertEqual('NOT_READY', self.prepare()[0]['status'])

    def test_no_mapping_is_not_all_cids_coverage(self):
        self.state['goal_contract']['body']['constraints'].pop()
        self.approve()
        result = self.prepare()[0]
        self.assertEqual('NOT_READY', result['status'])
        self.assertIn('Exactly one approved VISUAL_CASE_CRITERIA', result['reason'])

    def test_mapping_requires_exact_case_ids_and_nonempty_unique_approved_cids(self):
        for mapping in ({}, [], None, {'empty': ['C1']}, {'empty': ['C1'], 'filled': ['C2'], 'extra': ['C1']},
                        {'empty': ['C1'], 'filled': ['C404']}, {'empty': ['C1'], 'filled': []},
                        {'empty': ['C1'], 'filled': ['C2', 'C2']}, {'empty': ['C1'], 'filled': 'C2'},
                        {'empty': ['C1'], 'filled': [None]}, {'empty': ['C1'], 'filled': [{'id': 'C2'}]}):
            with self.subTest(mapping=mapping):
                self.state['goal_contract']['body']['constraints'][-1] = 'VISUAL_CASE_CRITERIA=' + json.dumps(mapping)
                self.approve()
                files = sorted(self.run.rglob('*'))
                result, command, env, prompt = self.prepare()
                self.assertEqual('NOT_READY', result['status'])
                self.assertFalse(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
                self.assertEqual((self.command, self.env, 'Validate the implementation'), (command, env, prompt))
                self.assertEqual(files, sorted(self.run.rglob('*')))
                self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_mapping_rejects_duplicate_markers_and_duplicate_decoded_json_keys(self):
        marker = self.state['goal_contract']['body']['constraints'][-1]
        for constraints, reason in (
            ([marker, marker], 'Exactly one approved'),
            ([marker, 'VISUAL_CASE_CRITERIA={"empty":["C2"],"filled":["C1"]}'], 'Exactly one approved'),
            (['VISUAL_CASE_CRITERIA={"empty":["C1"],"empty":["C1"],"filled":["C2"]}'], 'Duplicate JSON key'),
            (['VISUAL_CASE_CRITERIA={"empty":["C1"],"\\u0065mpty":["C2"],"filled":["C2"]}'], 'Duplicate JSON key')):
            with self.subTest(constraints=constraints):
                self.state['goal_contract']['body']['constraints'] = constraints
                self.approve()
                result = self.prepare()[0]
                self.assertEqual('NOT_READY', result['status'])
                self.assertIn(reason, result['reason'])

    def test_mapping_is_only_exact_marker_and_strict_json_not_prose_inference(self):
        marker = self.state['goal_contract']['body']['constraints'][-1]
        for text in ('Use ' + marker, ' ' + marker, marker + ' and accept the design',
                     'VISUAL_CASE_CRITERIA=', 'VISUAL_CASE_CRITERIA={empty:["C1"],filled:["C2"]}',
                     'VISUAL_CASE_CRITERIA={"empty":["C1"],"filled":[NaN]}',
                     'VISUAL_CASE_CRITERIA={"empty":["C1"],"filled":[Infinity]}'):
            with self.subTest(text=text):
                self.state['goal_contract']['body']['constraints'] = [text]
                self.approve()
                self.assertEqual('NOT_READY', self.prepare()[0]['status'])

    def test_unapproved_state_and_model_hints_neither_override_nor_supply_mapping(self):
        hinted = {'empty': ['C2'], 'filled': ['C1']}
        hints = {'case_criteria': hinted, 'VISUAL_CASE_CRITERIA': hinted,
                 'requirements': [{'id': 'R1', 'design_cases': ['empty', 'filled']}],
                 'constraints': ['VISUAL_CASE_CRITERIA=' + json.dumps(hinted)]}
        self.state.update(deepcopy(hints))
        self.state['settings'].update(deepcopy(hints))
        self.state['goal_contract'].update(deepcopy(hints))
        self.state['validation'] = {'verdict': 'PASS', **deepcopy(hints)}
        self.state['implementation'] = {'verdict': 'PASS', **deepcopy(hints)}
        self.review()
        receipt = self.accept()
        self.assertEqual({'empty': ['C1'], 'filled': ['C2']}, receipt['binding']['case_criteria'])
        self.assertTrue(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
        self.state['goal_contract']['body']['constraints'].pop()
        self.approve()
        self.assertEqual('NOT_READY', self.prepare()[0]['status'])
        self.assert_blocked(self.accept)

    def test_marker_tamper_or_reapproval_cannot_refresh_retained_contract_hash(self):
        self.review()
        receipt = self.accept()
        original_hash = self.state['goal_contract']['hash']
        original_receipts = deepcopy(self.state['visual_acceptance_receipts'])
        self.state['goal_contract']['body']['constraints'][-1] = (
            'VISUAL_CASE_CRITERIA={ "empty": ["C1"], "filled": ["C2"] }')
        self.assertEqual(original_hash, self.state['goal_contract']['hash'])
        self.assertFalse(contract.approved(self.state))
        self.assert_blocked(self.accept)
        self.assertFalse(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
        self.approve()
        self.assertNotEqual(original_hash, self.state['goal_contract']['hash'])
        self.assertTrue(contract.approved(self.state))
        self.assert_blocked(self.accept)
        self.assertFalse(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
        self.assertEqual(original_receipts, self.state['visual_acceptance_receipts'])
        self.assertEqual(original_hash, receipt['binding']['contract_hash'])
        self.assertEqual(original_hash, self.record['visual_runtime']['current']['contract_hash'])

    def test_unapproved_and_external_reference_roots_are_not_authority(self):
        self.state['user_events'] = []
        self.assertIn('authenticated approved contract', self.prepare()[0]['reason'])
        self.approve()
        self.state['settings']['design_manifest']['root'] = str(self.root.parent)
        self.assertEqual('NOT_READY', self.prepare()[0]['status'])

    def test_over_resizer_bounds_is_unknown_not_exact_original(self):
        self.limits['width'] = 1
        result = self.prepare()[0]
        self.assertEqual('NOT_READY', result['status'])
        self.assertIn('no-transform bounds', result['reason'])

    def test_prelaunch_changed_plugin_image_proof_configuration_or_record_is_rejected(self):
        context, command, env, _ = self.prepare()
        def verify():
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(), command=command, env=env)
        for path in (context['plugin_path'], context['images'][0]['path'], str(self.proof), context['launch_manifest']):
            with self.subTest(path=path):
                target = Path(path)
                saved = target.read_bytes()
                target.chmod(0o600)
                target.write_bytes(saved + b'changed')
                self.assert_blocked(verify)
                target.write_bytes(saved)
                target.chmod(0o400)
        env['OPENCODE_CONFIG_CONTENT'] = '{}'
        self.assert_blocked(verify)

    def test_event_report_plugin_image_capture_audit_tamper_invalidate_acceptance_and_projection(self):
        self.review()
        self.accept()
        context = self.record['visual_runtime']
        for path in (self.record['events'], self.record['output'], context['plugin_path'], context['images'][1]['path'],
                     self.captures['empty']['candidate_ref'], self.captures['filled']['capture_ref'], context['audit_path']):
            with self.subTest(path=path):
                target = Path(path)
                saved = target.read_bytes()
                target.chmod(0o600)
                target.write_bytes(saved + b'changed')
                self.assert_blocked(self.accept)
                self.assertFalse(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
                target.write_bytes(saved)
        self.assertTrue(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])

    def test_failed_repaired_foreign_session_and_forged_route_cannot_accept(self):
        self.review()
        for field, value in [('exit_code', 1), ('exit_code', True), ('report_only', True), ('report_repaired', True),
                             ('repaired_by', 'repair'), ('timed_out', True), ('interrupted', True), ('truncated_output', True),
                             ('expected_session', 'foreign'), ('thread_id', 'builder-session'), ('role', 'terra'),
                             ('launch_route', {'engine': 'opencode', 'model': 'model-report-claims-sol'}),
                             ('contract_hash', 'a' * 64), ('source_revision', 'b' * 64)]:
            with self.subTest(field=field, value=value):
                original = deepcopy(self.record)
                self.record[field] = value
                self.assert_blocked(self.accept)
                self.record.clear()
                self.record.update(original)
        self.assert_blocked(lambda: self.accept(validation=True))

    def test_changed_binding_and_forged_saved_record_remove_current_acceptance(self):
        self.review()
        self.accept()
        saved = deepcopy(self.state)
        for change in ('task', 'contract', 'mapping', 'criterion', 'record', 'route', 'source', 'runtime'):
            with self.subTest(change=change):
                self.state = deepcopy(saved)
                if change == 'task': self.state['current_task']['id'] = 'T2'
                if change == 'contract': self.state['goal_contract']['revision'] += 1; self.approve()
                if change == 'mapping':
                    self.state['goal_contract']['body']['constraints'][-1] = 'VISUAL_CASE_CRITERIA={"empty":["C2"],"filled":["C1"]}'
                    self.approve()
                if change == 'criterion': self.state['acceptance_criteria'][0]['verification_method'] = 'claim green'
                if change == 'record': self.state['stages'][-1]['exit_code'] = 1
                if change == 'route': self.state['settings']['roles']['sol']['model'] = 'other/model'
                if change == 'source': (self.root / 'greet.py').write_text('changed source\n')
                with patch.object(visual, '_runtime_hash', return_value='f' * 64) if change == 'runtime' else patch.object(visual, '_runtime_hash', wraps=visual._runtime_hash):
                    self.assertFalse(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])
                (self.root / 'greet.py').write_text('print("rendered implementation")\n')

    def test_mutable_verification_status_is_not_contract_identity(self):
        self.review()
        self.accept()
        self.state['acceptance_criteria'][0].update(status='verified', evidence=['functional report'])
        self.assertTrue(visual.projection(self.state, current_snapshot=self.snapshot())['current_all_accepted'])

    def test_missing_truncated_or_mismatched_delivery_never_mints_receipt(self):
        self.review()
        self.assert_blocked(lambda: self.accept(delivery=lambda *args, **kwargs: None))
        def wrong_images(*args, **kwargs):
            result = self.fake_delivery(*args, **kwargs)
            result['images'] = result['images'][:-1]
            return result
        self.assert_blocked(lambda: self.accept(delivery=wrong_images))
        with self.assertRaises(util.Paused):
            # The real transport verifier must reject our explicitly fake audit.
            visual.accept_review(None, self.state, self.record, run_dir=self.run,
                                 current_snapshot=self.snapshot(), accepted_validation=self.validation)
        self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_full_snapshot_gate_rejects_same_revision_with_different_files(self):
        self.review()
        changed = self.snapshot()
        changed['files']['greet.py'] = 'wrong'
        util.atomic_json(Path(self.record['before_ref']), changed)
        self.assert_blocked(self.accept)

    def test_truncated_or_failed_terminal_cannot_be_repaired_into_authority(self):
        self.review()
        for reason in ('length', 'error'):
            with self.subTest(reason=reason):
                self.save_report(reason=reason)
                self.record.pop('rework_evidence')
                reports.capture(self.record, self.report)
                self.validation['evidence_hashes'][self.record['events']] = util.file_hash(self.record['events'])
                self.assert_blocked(self.accept)
        path = Path(self.record['events'])
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        self.record.pop('rework_evidence')
        reports.capture(self.record, self.report)
        self.validation['evidence_hashes'][self.record['events']] = util.file_hash(self.record['events'])
        self.assert_blocked(self.accept)

    def test_capture_config_and_browser_proof_are_authenticated_not_builder_status(self):
        self.review()
        row = self.captures['empty']
        capture = util.read_object(Path(row['capture_ref']))
        browser = self.root / capture['artifacts']['browser']['path']
        config = self.root / capture['config_ref']
        for target in (browser, config):
            with self.subTest(target=target):
                saved = target.read_bytes()
                target.write_bytes(saved + b'changed')
                self.assert_blocked(self.accept)
                target.write_bytes(saved)
        self.builder['output'] = self.record['output']
        self.assert_blocked(self.accept)

    def test_wrong_case_cid_mapping_and_selected_capture_cannot_be_laundered_by_seal(self):
        self.review()
        self.report['design_results'][0]['criterion_ids'] = ['C2']
        self.save_report()
        self.record.pop('rework_evidence')
        reports.capture(self.record, self.report)
        self.validation.update(deepcopy(self.report))
        self.validation['evidence_hashes'][self.record['events']] = util.file_hash(self.record['events'])
        self.assert_blocked(self.accept)

    def test_only_exported_actual_sol_boundary_is_in_scope(self):
        for stage in ('terra', 'sol_report_repair', 'astra_checkpoint', 'review_change'):
            self.assertEqual((None, self.command, self.env, 'p'), visual.prepare(
                self.state, stage, self.root, self.run, self.base, self.command, self.env, 'p'))
        self.state['settings'].pop('design_manifest')
        self.assertEqual('NOT_READY', visual.projection(self.state, current_snapshot=self.snapshot())['status'])
        self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
        self.assertTrue(visual.completion_check(self.state, current_snapshot=self.snapshot())['required'])
        self.assertIsNone(visual.projection({}, current_snapshot={}))
        self.assertEqual({'required': False, 'passed': True, 'reason': None},
                         visual.completion_check({}, current_snapshot={}))

    def test_strict_missing_manifest_never_becomes_legacy_completion(self):
        for missing in (None, {}):
            with self.subTest(missing=missing):
                self.state['settings']['design_manifest'] = missing
                context, command, env, _ = self.prepare()
                self.assertEqual('NOT_READY', context['status'])
                self.assertEqual((self.command, self.env), (command, env))
                self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
                with self.assertRaises(util.Paused):
                    visual.verify_prelaunch(context, self.state, run_dir=self.run,
                                            current_snapshot=self.snapshot(), command=command, env=env)
                with self.assertRaises(util.Paused):
                    visual.accept_review(None, self.state, {'stage': 'sol'}, run_dir=self.run,
                                         current_snapshot=self.snapshot(), accepted_validation={})

    def test_not_ready_functional_review_never_requires_or_mints_visual_acceptance(self):
        original = deepcopy(self.state)
        for missing in ('captures', 'mapping', 'authority'):
            with self.subTest(missing=missing):
                self.state = deepcopy(original)
                if missing == 'captures':
                    with patch.object(visual.evidence, 'context', return_value=None):
                        context, command, env, prompt = self.prepare()
                elif missing == 'mapping':
                    self.state['goal_contract']['body']['constraints'] = ['VISUAL_REVIEW_PROFILE={}']
                    self.approve()
                    context, command, env, prompt = self.prepare()
                else:
                    context, command, env, prompt = self.prepare(launch_authority=None)
                self.assertEqual('NOT_READY', context['status'])
                self.assertEqual((self.command, self.env, 'Validate the implementation'), (command, env, prompt))
                before = deepcopy(self.state)
                with patch.object(visual.delivery, 'verify') as delivery:
                    self.assertIsNone(visual.accept_review(None, self.state,
                        {'stage': 'sol', 'visual_runtime': context}, run_dir=self.run,
                        current_snapshot=self.snapshot(), accepted_validation={'verdict': 'PASS'}))
                delivery.assert_not_called()
                self.assertEqual(before, self.state)
                self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))
                with self.assertRaises(util.Paused):
                    visual.require_completion(self.state, current_snapshot=self.snapshot())

    def test_functional_failures_keep_rework_without_requiring_visual_delivery(self):
        self.review(statuses=('NOT_VERIFIED', 'NOT_VERIFIED'))
        Path(self.record['visual_runtime']['audit_path']).unlink()
        for verdict in ('FAIL', 'BLOCKED'):
            with self.subTest(verdict=verdict):
                self.report['verdict'] = verdict
                self.save_report()
                self.record.pop('rework_evidence')
                reports.capture(self.record, self.report)
                validation = {**self.validation, **self.report,
                    'evidence_hashes': {self.record['events']: util.file_hash(self.record['events'])}}
                before = deepcopy(self.state)
                with patch.object(visual.delivery, 'verify') as delivery:
                    self.assertIsNone(visual.accept_review(None, self.state, self.record,
                        run_dir=self.run, current_snapshot=self.snapshot(), accepted_validation=validation))
                delivery.assert_not_called()
                self.assertEqual(before, self.state)
                self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))

    @contextlib.contextmanager
    def functional_launcher(self, *, before_admission=None):
        # The real common launcher and reference/capture policies are exercised.
        # Only provider/containment dependencies are stubbed; no client is run.
        self.state.update(iteration=1, sessions={}, next_stage='sol', version=2)
        self.state['settings'].update(transport_identity={'fixture': True})
        schema = self.run / 'functional-schema.json'
        util.atomic_json(schema, {'type': 'object', 'required': [], 'properties': {}})
        shutil.rmtree(self.root / '.autocode' / 'captures')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(autocode.provider_launch, 'prepare',
                return_value=(self.command, deepcopy(self.env), {}, {})))
            stack.enter_context(patch.object(autocode.provider_launch, 'verify_containment', side_effect=before_admission))
            stack.enter_context(patch.object(autocode.task_preflight, 'guard'))
            stack.enter_context(patch.object(autocode.output_policy, 'environment', return_value={}))
            stack.enter_context(patch.object(autocode.opencode, 'transport_drift', return_value=None))
            stack.enter_context(patch.object(autocode.opencode, 'local_settings', return_value={}))
            stack.enter_context(patch.object(autocode.support, 'snapshot', return_value=self.snapshot()))
            stack.enter_context(patch.object(autocode.readonly_events, 'prepare_opencode_snapshots'))
            popen = stack.enter_context(patch.object(autocode.supervision, 'launch',
                side_effect=RuntimeError('functional request admitted')))
            yield schema, popen

    def test_common_launcher_runs_functional_validator_when_visual_captures_are_missing(self):
        with self.functional_launcher() as (schema, popen):
            with self.assertRaisesRegex(RuntimeError, 'functional request admitted'):
                autocode.run_role(state=self.state, workspace=self.root, run_dir=self.run, role='sol', sandbox='read-only',
                    schema=schema, prompt='Functional validation only', model='openai/reviewer', allow_write=False, dry_run=False)
            popen.assert_called_once()
            self.assertEqual(self.command, popen.call_args.args[0])
            self.assertEqual(self.env, popen.call_args.kwargs['env'])
        self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))

    def test_common_launcher_runs_functional_validator_when_visual_mapping_is_missing(self):
        constraints = self.state['goal_contract']['body']['constraints']
        constraints[:] = [row for row in constraints if not row.startswith('VISUAL_CASE_CRITERIA=')]
        constraints.append('VISUAL_REVIEW_PROFILE={}')
        self.approve()
        with self.functional_launcher() as (schema, popen):
            with self.assertRaisesRegex(RuntimeError, 'functional request admitted'):
                autocode.run_role(state=self.state, workspace=self.root, run_dir=self.run, role='sol', sandbox='read-only',
                    schema=schema, prompt='Functional validation only', model='openai/reviewer', allow_write=False, dry_run=False)
            popen.assert_called_once()
            self.assertEqual(self.command, popen.call_args.args[0])
            self.assertEqual(self.env, popen.call_args.kwargs['env'])
        self.assertFalse(visual.completion_allowed(self.state, current_snapshot=self.snapshot()))

    def test_common_launcher_refuses_invalid_retained_references_before_preparation(self):
        record = deepcopy(self.state['settings']['design_manifest'])
        reference = Path(record['root']) / record['body']['cases'][0]['artifacts']['screenshot']['path']
        original = reference.read_bytes()
        alias = self.root / '.autocode' / 'reference-alias'
        alias.symlink_to(Path(record['root']), target_is_directory=True)
        changes = {
            'malformed record': lambda: self.state['settings'].update(design_manifest=['not a record']),
            'missing body': lambda: self.state['settings']['design_manifest'].pop('body'),
            'invalid schema': lambda: self.state['settings']['design_manifest']['body'].update(cases=[]),
            'changed identity': lambda: self.state['settings']['design_manifest'].update(manifest_hash='0' * 64),
            'changed reference': lambda: reference.write_bytes(b'changed reference'),
            'missing reference': lambda: reference.unlink(),
            'external root': lambda: self.state['settings']['design_manifest'].update(root=str(self.root.parent)),
            'symlinked root': lambda: self.state['settings']['design_manifest'].update(root=str(alias)),
        }
        self.state.update(iteration=1, sessions={}, next_stage='sol')
        for label, change in changes.items():
            with self.subTest(label=label):
                self.state['settings']['design_manifest'] = deepcopy(record)
                reference.write_bytes(original)
                change()
                with patch.object(autocode.provider_launch, 'prepare') as prepare, \
                        patch.object(autocode.subprocess, 'Popen') as popen, self.assertRaises(util.Paused) as caught:
                    autocode.run_role(state=self.state, workspace=self.root, run_dir=self.run, role='sol', sandbox='read-only',
                        schema=self.run / 'unused-schema.json', prompt='Retained reference integrity',
                        model='openai/reviewer', allow_write=False, dry_run=False)
                self.assertEqual('PAUSED_VISUAL_EVIDENCE', caught.exception.status)
                prepare.assert_not_called()
                popen.assert_not_called()

    def test_common_launcher_rechecks_not_ready_reference_immediately_before_popen(self):
        manifest = self.state['settings']['design_manifest']
        reference = Path(manifest['root']) / manifest['body']['cases'][0]['artifacts']['screenshot']['path']
        def change_after_preparation(worker):
            reference.write_bytes(b'changed after provider preparation')
        with self.functional_launcher(before_admission=change_after_preparation) as (schema, popen):
            with self.assertRaises(util.Paused) as caught:
                autocode.run_role(state=self.state, workspace=self.root, run_dir=self.run, role='sol', sandbox='read-only',
                    schema=schema, prompt='Retained reference integrity', model='openai/reviewer', allow_write=False, dry_run=False)
            self.assertEqual('PAUSED_VISUAL_EVIDENCE', caught.exception.status)
            self.assertIn('changed design reference', str(caught.exception))
            popen.assert_not_called()

    def test_common_launcher_holds_missing_manifest_before_provider_preparation(self):
        self.state['settings'].pop('design_manifest')
        self.state.update(iteration=1, sessions={}, next_stage='sol')
        with patch.object(autocode.provider_launch, 'prepare') as prepare, \
                patch.object(autocode.subprocess, 'Popen') as popen, self.assertRaises(util.Paused) as caught:
            autocode.run_role(state=self.state, workspace=self.root, run_dir=self.run, role='sol', sandbox='read-only',
                              schema=self.run / 'unused-schema.json', prompt='No visual manifest',
                              model='openai/reviewer', allow_write=False, dry_run=False)
        self.assertEqual('PAUSED_VISUAL_EVIDENCE', caught.exception.status)
        prepare.assert_not_called()
        popen.assert_not_called()

    def test_status_cannot_count_strict_missing_manifest_as_current_completion(self):
        self.state['settings'].pop('design_manifest')
        self.state.update(status='TASK_COMPLETE', iteration=1, sessions={})
        output = io.StringIO()
        with patch.object(autocode.completion_gate, 'completion_ready', return_value=True), \
                patch.object(autocode.support, 'snapshot', return_value=self.snapshot()), \
                contextlib.redirect_stdout(output):
            autocode_status_command.render(autocode, self.state, SimpleNamespace(run_dir=self.run, engine='opencode'),
                                            self.root, self.run)
        status = json.loads(output.getvalue())
        self.assertFalse(status['completion_current'])
        self.assertEqual(0, status['view']['efficiency']['delivery']['verified_deliveries'])


class VisualRuntimeCliTests(unittest.TestCase):
    """Real TaskRun flow through exact hashed fake clients; no image authority.

    The existing explicit OpenCode fixture bootstrap simulates containment only
    for these pinned script copies. It cannot select an actual model client and
    is never installed or activated by production environment settings.
    """
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='visual-runtime-cli-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.test', 'commit', '-q', '--allow-empty', '-m', 'fixture'], check=True)
        repository = getattr(self, 'repository', Path(__file__).resolve().parents[1])
        binary = self.root / 'fixture-bin'
        binary.mkdir()
        for name, source in (('opencode', 'fake_opencode.py'), ('codex', 'fake_codex.py'),
                             ('goal_fixtures.py', 'goal_fixtures.py')):
            shutil.copy2(repository / 'tools' / source, binary / name)
        variant = binary / 'codex'
        source = variant.read_text()
        marker = 'Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))'
        before, separator, after = source.rpartition(marker)
        self.assertTrue(separator)
        hook = r"""
design = data.get('design_manifest')
if design:
    design_body = design.get('body') or json.loads(Path(design['full_manifest']).read_text())
    cases = design_body['cases']
    if 'contract' in result:
        mapping = {case['id']: ['C2' if mode == 'milestones' and index == 1 else 'C1']
                   for index, case in enumerate(cases)}
        declaration = 'VISUAL_CASE_CRITERIA=' + json.dumps(mapping, sort_keys=True)
        result['contract']['constraints'] = [row for row in result['contract']['constraints']
            if not row.startswith('VISUAL_CASE_CRITERIA=')] + [declaration]
    if stage in ('sol', 'astra_checkpoint'):
        result['design_manifest_hash'] = design['manifest_hash']
        result['design_results'] = [dict(id=case['id'], status='NOT_VERIFIED',
            criterion_ids=['C2' if mode == 'milestones' and index == 1 else 'C1'],
            candidate_ref='', comparison_ref='', capture_ref='', capture_sha256='')
            for index, case in enumerate(cases)]
"""
        variant.write_text(before + hook + marker + after)
        for name in ('opencode', 'codex'):
            (binary / name).chmod(0o755)
        hashes = {name: util.file_hash(binary / name) for name in ('opencode', 'codex', 'goal_fixtures.py')}
        bootstrap = self.root / 'offline-visual-cli.py'
        bootstrap.write_text('''import os
from pathlib import Path
import hashlib
import shutil
import sys
sys.path.insert(0, ''' + repr(str(repository)) + ''')
from tests import opencode_fixture_cli as fixture
EXPECTED = ''' + repr(hashes) + '''
def exact_fixture(executable="opencode", *, env=None):
    environment = os.environ if env is None else env
    selected = shutil.which(str(executable), path=environment.get("PATH", ""))
    if not selected:
        raise RuntimeError("Missing exact offline fixture")
    selected = Path(selected).resolve()
    for name, expected in EXPECTED.items():
        path = selected if name == "opencode" else selected.with_name(name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Offline visual bootstrap refuses unknown client bytes: " + name)
    return selected
fixture.checked_fixture = exact_fixture
fixture.main()
''')
        self.command = (sys.executable, str(bootstrap), str(repository / 'tools' / 'autocode.py'))
        self.options = ('--engine', 'opencode', '--joint-planning', '--max-parallel-builders', '1')
        self.probe = self.root / 'launches.jsonl'
        self.env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                    'AUTOCODE_HOME': str(self.root / 'registry'), 'XDG_CONFIG_HOME': str(self.root / 'config'),
                    'CODEX_HOME': str(self.root / 'codex-config'), 'PYTHONDONTWRITEBYTECODE': '1',
                    'AUTOCODE_REGISTRY_LAUNCH_PROBE': str(self.probe)}
        for name in ('AUTOCODE_PROVIDER', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENCODE_CONFIG_CONTENT'):
            self.env.pop(name, None)
        exports = self.root / 'exports'
        exports.mkdir()
        png(exports / 'reference.png', 2, 1)
        (exports / 'context.json').write_text('{"offline_fixture":true}')
        artifacts = {kind: {'path': name, 'sha256': util.file_hash(exports / name)}
                     for kind, name in (('screenshot', 'reference.png'), ('design_context', 'context.json'))}
        cases = [{'id': name, 'file_key': 'Fixture', 'node_id': '1:2', 'state': name, 'route': '/' + name,
                  'implementation_paths': ['greet.py'], 'viewport': {'width': 2, 'height': 1, 'device_scale_factor': 1},
                  'export_scale': 1, 'artifacts': deepcopy(artifacts)} for name in ('empty', 'filled')]
        self.manifest = exports / 'manifest.json'
        self.manifest.write_text(json.dumps({'version': 1, 'files': [{'key': 'Fixture', 'nodes': ['1:2']}], 'cases': cases}))

    def approved_run(self, mode):
        self.env['AUTOCODE_FIXTURE_MODE'] = mode
        run = taskrun.TaskRun.start(self.project, 'Build a greeting tool', command=self.command,
            options=self.options, start_options=('--figma-manifest', str(self.manifest)), env=self.env, timeout=120)
        view = run.status()
        self.assertEqual('answer', view['needs']['kind'], view)
        run.answer('Q1', 'CLI')
        view = run.advance_until_input()
        self.assertEqual('approve_plan', view['needs']['kind'], view)
        run.approve_plan(view['needs']['token'])
        return run

    def launched_stages(self):
        return [json.loads(line)['stage'] for line in self.probe.read_text().splitlines()]

    def test_public_taskrun_advances_functional_milestones_but_refuses_unverified_visual_completion(self):
        run = self.approved_run('milestones')
        view = run.advance_until_input()
        self.assertFalse(view['done'], view)
        stages = self.launched_stages()
        self.assertGreaterEqual(stages.count('sol'), 2, (stages, view['status'], view['stop_reason']))
        self.assertGreaterEqual(stages.count('terra'), 2, stages)
        self.assertIn('astra_review', stages)
        self.assertTrue((self.project / 'bye.py').is_file(), view)
        self.assertEqual(['empty', 'filled'], view['design']['not_passing'])
        self.assertIsNone(view['design']['current_visual_acceptance'])
        self.assertEqual(0, view['efficiency']['delivery']['verified_deliveries'])
        self.assertIn('Current independent image-delivery and visual-acceptance evidence is required', view['stop_reason'])

    def test_public_taskrun_routes_functional_failure_through_rework_before_visual_completion(self):
        run = self.approved_run('rework')
        view = run.advance_until_input()
        self.assertFalse(view['done'], view)
        stages = self.launched_stages()
        self.assertGreaterEqual(stages.count('sol'), 2, (stages, view['status'], view['stop_reason']))
        self.assertGreaterEqual(stages.count('terra'), 2, stages)
        validations = [json.loads(path.read_text()) for path in sorted(run.run_dir.glob('iterations/*/validator-*.json'))
                       if path.name.endswith('.json') and not path.name.endswith(('.before.json', '.after.json', '.schema.json', '.tools.json'))]
        failed = [report for report in validations if report.get('verdict') == 'FAIL']
        self.assertTrue(failed, validations)
        self.assertTrue(any('Empty names are accepted' in finding['finding'] for report in failed for finding in report['findings']))
        self.assertTrue(any(report.get('verdict') == 'PASS' for report in validations), validations)
        self.assertIn('Current independent image-delivery and visual-acceptance evidence is required', view['stop_reason'])
        self.assertEqual(['empty', 'filled'], view['design']['not_passing'])
        self.assertIsNone(view['design']['current_visual_acceptance'])
        self.assertEqual(0, view['efficiency']['delivery']['verified_deliveries'])
