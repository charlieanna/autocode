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
        return {'evidence_hashes': {str(self.proof): util.file_hash(self.proof)}, 'image_limits': self.limits}

    def prepare(self, **options):
        options = {'launch_authority': self.qualified_config, 'current_snapshot': self.snapshot(), **options}
        return visual.prepare(self.state, 'sol', self.root, self.run, self.base,
                               self.command, self.env, 'Validate the implementation', **options)

    def test_authorized_environment_is_used_and_bound_without_persisting_secrets(self):
        def qualified(**kwargs):
            result = self.qualified_config(**kwargs)
            result['environment'] = {**kwargs['env'], 'HOME': str(self.run / 'qualified-home')}
            return result
        context, command, environment, _ = self.prepare(launch_authority=qualified)
        self.assertEqual('READY', context['status'])
        self.assertEqual(str(self.run / 'qualified-home'), environment['HOME'])
        self.assertEqual(1, context['audit_options']['max_requests'])
        self.assertEqual('autocode_sol', context['audit_options']['reviewer']['agent'])
        self.assertNotIn('fixture-unchanged-never-written', Path(context['launch_manifest']).read_text())
        visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(),
                                command=command, env=environment)
        with self.assertRaises(util.Paused):
            visual.verify_prelaunch(context, self.state, run_dir=self.run, current_snapshot=self.snapshot(),
                                    command=command, env={**environment, 'HOME': '/unapproved'})

    def test_qualified_child_executable_is_rechecked_before_launch(self):
        executable = self.run / 'qualified-opencode'
        executable.write_text('qualified executable bytes')
        def qualified(**kwargs):
            result = self.qualified_config(**kwargs)
            result['child_identity'] = {'executable': str(executable), 'executable_sha256': util.file_hash(executable),
                                       'command_sha256': util.digest(kwargs['command']),
                                       'environment_sha256': util.digest(kwargs['env'])}
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
