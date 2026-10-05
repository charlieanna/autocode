"""Visual authority controls; generated PNGs are not Figma integration evidence."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import uuid
import zlib

import autocode_visual_acceptance as visual
import autocode_image_delivery as delivery
import autocode_util as util


def png(rgb, width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    pixels = b''.join(b'\0' + bytes(rgb) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def native_read_probe(root):
    """Explicit manual, model-free probe; never run by unittest discovery.

    Preserve effective native permissions. debug auto-approves asks, so inspect
    the actual policy before every tool call and refuse anything but allow.
    """
    from autocode_preflight_worker import permission_match
    root = Path(root).resolve()
    directory = root / '.scenario-runs' / ('visual-transport-probe-' + uuid.uuid4().hex)
    directory.mkdir()
    agent = 'autocode_visual_probe_' + uuid.uuid4().hex
    env = dict(os.environ)
    config = json.loads(env.get('OPENCODE_CONFIG_CONTENT', '{}'))
    config.setdefault('agent', {})[agent] = {
        'mode': 'primary', 'model': 'openai/gpt-6-sol',
        'permission': {'edit': 'deny', 'bash': 'deny', 'task': 'deny', 'question': 'deny',
                       'external_directory': 'deny'}}
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    prefix = ['opencode', 'debug', 'agent', agent]
    policy_call = subprocess.run(prefix, cwd=root, env=env, capture_output=True, text=True, timeout=60)
    if policy_call.returncode:
        raise ValueError('Cannot inspect native read policy')
    policy = json.loads(policy_call.stdout)
    rules = policy.get('permission')
    if not isinstance(rules, list) or policy.get('tools', {}).get('read') is not True:
        raise ValueError('Native read is unavailable under the actual policy')
    result = {'version': subprocess.check_output(['opencode', '--version'], text=True).strip(),
              'model_calls': 0, 'delivery_verified': False, 'images': []}
    for label, color in [('reference', (210, 20, 10)), ('candidate', (10, 40, 230))]:
        path = directory / (label + '.png')
        path.write_bytes(png(color))
        matching = [rule for rule in rules if permission_match('read', rule['permission'])
                    and permission_match(str(path.relative_to(root)), rule['pattern'])]
        if not matching or matching[-1]['action'] != 'allow':
            raise ValueError('Probe refuses denied, unknown or approval-bearing read policy')
        call = subprocess.run([*prefix, '--tool', 'read', '--params', json.dumps({'filePath': str(path)})],
                              cwd=root, env=env, capture_output=True, text=True, timeout=60)
        if call.returncode:
            raise ValueError('Native read tool failed')
        body = json.loads(call.stdout)
        # Retain actual native output for schema inspection, not delivery authority.
        (directory / (label + '-read.json')).write_text(call.stdout)
        attachments = body['result'].get('attachments', [])
        observed = []
        for attachment in attachments:
            url = attachment.get('url', '')
            header, separator, payload = url.partition(';base64,')
            if not separator:
                raise ValueError('Native attachment is not a base64 data URL')
            data = base64.b64decode(payload, validate=True)
            observed.append({'sha256': hashlib.sha256(data).hexdigest(), 'mime': header.removeprefix('data:'),
                             'matches_fixture': data == path.read_bytes(), 'keys': sorted(attachment)})
        result['images'].append({'label': label, 'result_keys': sorted(body['result']),
                                 'metadata': body['result'].get('metadata'), 'attachments': observed})
    (directory / 'observations.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'directory': str(directory), **result}, indent=2))


def native_request_probe(root, config_dir=None):
    """Manual offline CLI conformance: an in-process fake HTTP provider, no model.

    Requires a coordinator-provided already bootstrapped, fixture-local config;
    OpenCode otherwise implicitly installs its plugin SDK even for a local plugin.
    The only provider is a local fake; default auth plugins/downloads are disabled.
    """
    root = Path(root).resolve()
    if config_dir is None:
        raise ValueError('Coordinator must supply a fixture-local bootstrapped config; no implicit installs')
    config_dir = Path(config_dir).resolve()
    if not config_dir.is_relative_to(root / '.scenario-runs') or not (config_dir / 'opencode' / 'node_modules').is_dir():
        raise ValueError('Config must be an existing owned probe fixture')
    directory = root / '.scenario-runs' / ('visual-request-probe-' + uuid.uuid4().hex)
    directory.mkdir()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw_body = self.rfile.read(int(self.headers['Content-Length']))
            body = json.loads(raw_body)
            payloads = []
            for message in body.get('messages', []):
                for part in message.get('content', []) if isinstance(message.get('content'), list) else []:
                    if part.get('type') == 'image_url':
                        url = part['image_url']['url']
                        prefix, payload = url.split(';base64,', 1)
                        data = base64.b64decode(payload, validate=True)
                        payloads.append({'mime': prefix.removeprefix('data:'), 'sha256': hashlib.sha256(data).hexdigest()})
            requests.append({'path': self.path, 'model': body.get('model'), 'images': payloads,
                             'body_sha256': hashlib.sha256(raw_body).hexdigest(),
                             'request_id': self.headers.get('x-autocode-image-request')})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('x-request-id', 'local-http-request')
            self.end_headers()
            for chunk in [
                {'id': 'local-response', 'object': 'chat.completion.chunk', 'created': 1791075600,
                 'model': 'reviewer', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '{"verdict":"PASS"}'},
                                                  'finish_reason': None}]},
                {'id': 'local-response', 'object': 'chat.completion.chunk', 'created': 1791075600,
                 'model': 'reviewer', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}},
            ]:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    images = []
    for label, color in [('reference', (210, 20, 10)), ('candidate', (10, 40, 230))]:
        path = directory / (label + '.png')
        path.write_bytes(png(color))
        images.append(str(path))
    env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG') if key in os.environ}
    for key, folder in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                        ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'),
                        ('OPENCODE_TEST_MANAGED_CONFIG_DIR', 'managed')]:
        path = directory / folder
        path.mkdir()
        env[key] = str(path)
    config = {'$schema': 'https://opencode.ai/config.json', 'autoupdate': False, 'share': 'disabled',
              'enabled_providers': ['visual-probe'], 'small_model': 'visual-probe/reviewer',
              'plugin': [(root / 'tools' / 'autocode_image_delivery.mjs').as_uri()],
              'provider': {'visual-probe': {'npm': '@ai-sdk/openai-compatible', 'name': 'Offline image probe',
                            'options': {'baseURL': f'http://127.0.0.1:{server.server_port}/v1', 'apiKey': 'fixture-not-a-secret'},
                            'models': {'reviewer': {'name': 'Offline reviewer', 'attachment': True,
                                       'modalities': {'input': ['text', 'image'], 'output': ['text']},
                                       'limit': {'context': 16000, 'output': 1000}}}}},
              'permission': 'deny', 'agent': {'visual_probe': {'mode': 'primary', 'permission': 'deny'},
                                              'title': {'disable': True}, 'summary': {'disable': True}}}
    probe_binding = {'reviewer': {'provider': 'opencode', 'model': 'visual-probe/reviewer'}}
    env.update(OPENCODE_CONFIG_CONTENT=json.dumps(config), OPENCODE_DISABLE_DEFAULT_PLUGINS='1',
               OPENCODE_DISABLE_MODELS_FETCH='1', OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_PROJECT_CONFIG='1',
               XDG_CONFIG_HOME=str(config_dir), npm_config_offline='true',
               AUTOCODE_IMAGE_AUDIT=json.dumps({'path': str(directory / 'audit.jsonl'),
                    'attempt_id': 'offline-conformance', 'binding_sha256': util.digest(probe_binding),
                    'max_requests': 1, 'reviewer': {**probe_binding['reviewer'], 'agent': 'visual_probe'}}))
    try:
        command = ['opencode', 'run', '--dir', str(directory), '--format', 'json',
                   '--agent', 'visual_probe', '--model', 'visual-probe/reviewer', '--title', 'Offline image probe',
                   '--file', *images, '--', 'Return the fixture response. Do not call tools.']
        with subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, start_new_session=True) as child:
            try:
                stdout, stderr = child.communicate(timeout=150)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                stdout, stderr = child.communicate()
            code = child.returncode
        (directory / 'events.jsonl').write_text(stdout)
        (directory / 'stderr.txt').write_text(stderr)
        (directory / 'server-observations.json').write_text(json.dumps(requests, indent=2) + '\n')
        verified = False
        if code == 0:
            expected = [{'case_id': 'fixture', 'kind': kind, 'sha256': util.file_hash(path), 'mime': 'image/png'}
                        for kind, path in zip(('reference', 'candidate'), images)]
            proof = delivery.verify({}, events=stdout.encode(), report=b'{"verdict":"PASS"}', images=expected,
                                    binding=probe_binding, audit_path=directory / 'audit.jsonl',
                                    attempt_id='offline-conformance', plugin_sha256=util.file_hash(root / 'tools' / 'autocode_image_delivery.mjs'))
            observed = next(row for row in requests if row['request_id'] == proof['request_id'])
            verified = (observed['body_sha256'] == proof['request_body_sha256']
                        and observed['images'] == [{key: row[key] for key in ('mime', 'sha256')} for row in expected])
            (directory / 'verified-transport.json').write_text(json.dumps({'transport_verified': verified,
                                'visual_acceptance': 'NOT_VERIFIED', 'proof': proof}, indent=2) + '\n')
        print(json.dumps({'directory': str(directory), 'exit_code': code, 'live_model_calls': 0,
                          'transport_verified': verified, 'requests': requests, 'stderr': stderr}, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class VisualAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.run = self.root / '.autocode' / 'runs' / 'visual'
        self.run.mkdir(parents=True)
        self.manifest_path = self.root / 'manifest.json'
        self.source = util.digest('source')
        self.captures = {}
        cases, self.results = [], []
        for index, state in enumerate(('empty', 'populated')):
            reference = self.root / (state + '-reference.png')
            reference.write_bytes(png((210, 20, index)))
            context = self.root / (state + '-context.json')
            context.write_text(json.dumps({'state': state}))
            artifacts = {key: {'path': path.name, 'sha256': util.file_hash(path)}
                         for key, path in [('screenshot', reference), ('design_context', context)]}
            case = {'id': state, 'file_key': 'SyntheticFixture', 'node_id': '1:2', 'state': state,
                    'route': '/' + state, 'implementation_paths': ['app.html'],
                    'viewport': {'width': 2, 'height': 2, 'device_scale_factor': 1},
                    'export_scale': 1, 'artifacts': artifacts}
            cases.append(case)
        body = {'version': 1, 'files': [{'key': 'SyntheticFixture', 'nodes': ['1:2']}], 'cases': cases}
        self.manifest_path.write_text(json.dumps(body, indent=2))
        manifest = {'root': str(self.root), 'body': body, 'manifest_hash': util.digest(body)}
        self.state = {'workspace': str(self.root), 'settings': {'design_manifest': manifest}}
        self.current = {'task_id': 'T1', 'contract_revision': 1, 'contract_hash': util.digest('contract'),
                        'source_revision': self.source, 'runtime_hash': util.digest('runtime'),
                        'criteria': [{'id': 'C1', 'criterion': 'Both states match the approved design',
                                      'verification_method': 'Independent inspection of both images per state'}],
                        'case_criteria': {'empty': ['C1'], 'populated': ['C1']},
                        'reviewer': {'provider': 'opencode', 'model': 'openai/gpt-6-sol'},
                        'manifest_body_hash': manifest['manifest_hash'],
                        'manifest_file_sha256': util.file_hash(self.manifest_path)}
        for index, case in enumerate(cases):
            capture_root = self.root / '.autocode' / 'captures' / case['id']
            capture_root.mkdir(parents=True)
            candidate = capture_root / 'candidate.png'
            candidate.write_bytes(png((10, 40, 230 - index)))
            capture = {'version': 1, 'reference_hash': manifest['manifest_hash'], 'source_revision': self.source,
                       'case': {key: deepcopy(case[key]) for key in ('id', 'route', 'state', 'viewport')},
                       'artifacts': {'candidate': {'path': str(candidate.relative_to(self.root)),
                                                   'sha256': util.file_hash(candidate)}}}
            path = capture_root / 'manifest.json'
            path.write_text(json.dumps(capture))
            self.captures[str(path)] = capture
            self.results.append({'id': case['id'], 'status': 'PASS', 'criterion_ids': ['C1'],
                                 'capture_ref': str(path), 'capture_sha256': util.file_hash(path),
                                 'candidate_ref': str(candidate)})
        self.report = {key: self.current[key] for key in ('task_id', 'contract_revision', 'contract_hash')}
        self.report.update(verdict='PASS', design_manifest_hash=manifest['manifest_hash'], design_results=self.results)
        self.record = {'stage': 'sol', 'events': str(self.run / 'events.jsonl'), 'output': str(self.run / 'report.json'),
                       'finished_at': '2026-10-04T01:00:00+00:00', 'engine': 'opencode', 'exit_code': 0,
                       **{key: self.current[key] for key in ('task_id', 'contract_revision', 'contract_hash', 'source_revision')}}
        self.stage = {'stage': 'sol', 'accepted': True, 'read_only': True, 'independent': True,
                      'source_full_gate': True, 'provider': 'opencode', 'model': 'openai/gpt-6-sol',
                      'session_id': 'ses_reviewer', 'producer_session_id': 'ses_builder',
                      'producer_model': 'zai-coding-plan/glm-5.3', 'runtime_hash': self.current['runtime_hash'],
                      'finished_at': self.record['finished_at']}
        self.delivery_changes = {}
        self.save_report()

    def save_report(self, reason='stop'):
        Path(self.record['output']).write_text(json.dumps(self.report))
        self.events = [self.event('step_start', 'start'), self.event('text', 'text', text=json.dumps(self.report)),
                       self.event('step_finish', 'finish', reason=reason)]
        self.save_events()

    def event(self, kind, identity, **fields):
        return {'type': kind, 'sessionID': 'ses_reviewer', 'timestamp': 1791075600000,
                'part': {'id': identity, 'messageID': 'msg_reviewer', 'sessionID': 'ses_reviewer', **fields}}

    def save_events(self):
        Path(self.record['events']).write_text(''.join(json.dumps(row) + '\n' for row in self.events))

    def capture(self, ref, sha, **kwargs):
        # Controlled capture authority, not a browser capture qualification.
        if util.file_hash(ref) != sha:
            raise ValueError('Changed capture')
        return deepcopy(self.captures[ref]), [ref]

    def delivery(self, record, *, events, report, images, binding):
        # Normalized boundary contract only. These are NOT real provider receipts.
        path = self.run / 'controlled-transport.json'
        proof = {'version': 1, 'kind': 'final_request_image_delivery', 'image_capable': True,
                 'completed': True, 'after_final_transform': True, 'session_id': 'ses_reviewer',
                 'message_id': 'msg_reviewer', 'finish_id': 'finish', **binding['reviewer'],
                 'binding_sha256': util.digest(binding), 'events_sha256': hashlib.sha256(events).hexdigest(),
                 'report_sha256': hashlib.sha256(report).hexdigest(), 'request_id': 'native-request-1',
                 'response_id': 'native-response-1', 'images': images}
        proof.update(deepcopy(self.delivery_changes))
        path.write_text(json.dumps(proof))
        return {**proof, 'evidence_ref': str(path), 'evidence_sha256': util.file_hash(path)}

    def accept(self, **overrides):
        options = {'run_dir': self.run, 'current': self.current, 'manifest_path': self.manifest_path,
                   'verify_stage': lambda record, current: deepcopy(self.stage),
                   'verify_capture': self.capture, 'verify_delivery': self.delivery}
        return visual.accept(self.state, self.record, **(options | overrides))

    def summary(self, **overrides):
        receipts = self.state.get('visual_acceptance_receipts', [])
        options = {'current': self.current, 'manifest': self.state['settings']['design_manifest'],
                   'evidence_hashes': {path: util.file_hash(path) for receipt in receipts
                                       for path in receipt['evidence_hashes'] if Path(path).is_file()},
                   'verified_capture_hashes': [row['capture_sha256'] for row in self.results]}
        return visual.summary(receipts, **(options | overrides))

    def test_controlled_delivery_accepts_once_and_is_not_a_native_qualification(self):
        receipt = self.accept()
        self.assertEqual(receipt, self.accept())
        self.assertEqual(1, len(self.state['visual_acceptance_receipts']))
        self.assertNotEqual(receipt['binding']['manifest_body_hash'], receipt['binding']['manifest_file_sha256'])
        self.assertEqual(self.record['finished_at'], receipt['accepted_at'])
        projected = self.summary()
        self.assertEqual(2, projected['accepted_cases'])
        self.assertEqual(1, projected['accepted_frames'])
        self.assertTrue(projected['current_all_accepted'])
        projected['cases'][0]['criterion_ids'].append('foreign')
        self.assertEqual(['C1'], self.summary()['cases'][0]['criterion_ids'])

    def test_native_debug_attachment_bytes_do_not_prove_delivery(self):
        # Actual 1.18.33 debug read shape, observed model-free on these PNG bytes.
        attachments = [{'type': 'file', 'mime': 'image/png',
                        'url': 'data:image/png;base64,' + base64.b64encode(png(color)).decode()}
                       for color in [(210, 20, 10), (10, 40, 230)]]
        self.assertNotEqual(*(hashlib.sha256(base64.b64decode(row['url'].split(',')[1])).hexdigest()
                              for row in attachments))
        self.report['image_reads'] = {'metadata': {'truncated': False, 'loaded': []}, 'attachments': attachments}
        self.report['independent_visual_review'] = 'PASS'
        self.save_report()
        self.assertIsNone(self.accept(verify_delivery=None))
        self.assertNotIn('visual_acceptance_receipts', self.state)
        self.assertFalse(self.summary()['coverage_complete'])

    def test_deterministic_pixel_pass_and_captured_bundle_are_not_authority(self):
        self.report.update(pixel_status='PASS', independent_visual_review='NOT_PERFORMED')
        self.save_report()
        self.assertIsNone(self.accept(verify_delivery=None))
        self.assertEqual(0, self.summary()['accepted_cases'])

    def test_missing_one_or_wrong_delivered_payload_is_rejected(self):
        original = self.delivery
        for fault in ('missing', 'wrong_bytes', 'wrong_case', 'wrong_mime', 'duplicate'):
            with self.subTest(fault=fault):
                def changed(*args, **kwargs):
                    proof = original(*args, **kwargs)
                    if fault == 'missing':
                        proof['images'].pop()
                    elif fault == 'duplicate':
                        proof['images'][1] = deepcopy(proof['images'][0])
                    else:
                        key, value = {'wrong_bytes': ('sha256', '0' * 64), 'wrong_case': ('case_id', 'foreign'),
                                      'wrong_mime': ('mime', 'text/plain')}[fault]
                        proof['images'][0][key] = value
                    return proof
                with self.assertRaises(ValueError):
                    self.accept(verify_delivery=changed)
                self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_failed_unsupported_pretransform_or_foreign_delivery_is_rejected(self):
        faults = {'completed': False, 'image_capable': False, 'after_final_transform': False,
                  'kind': 'tool_read', 'session_id': 'ses_builder', 'message_id': 'wrong', 'finish_id': 'wrong',
                  'model': 'other/model', 'provider': 'foreign', 'binding_sha256': '0' * 64,
                  'events_sha256': '0' * 64, 'report_sha256': '0' * 64, 'request_id': '', 'response_id': ''}
        for key, value in faults.items():
            with self.subTest(field=key):
                self.delivery_changes = {key: value}
                with self.assertRaises(ValueError):
                    self.accept()
                self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_stage_independence_readonly_finish_and_source_gate_are_required(self):
        original = deepcopy(self.stage)
        for key, value in {'accepted': False, 'read_only': False, 'independent': False, 'source_full_gate': False,
                           'model': self.stage['producer_model'], 'session_id': 'ses_builder', 'stage': 'terra',
                           'runtime_hash': '0' * 64, 'finished_at': '2026-10-04T01:01:00+00:00'}.items():
            with self.subTest(field=key):
                self.stage = original | {key: value}
                with self.assertRaises(ValueError):
                    self.accept()
        self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_finished_stream_cannot_override_failed_or_wrong_stage_record(self):
        original = deepcopy(self.record)
        for key, value in {'exit_code': -9, 'timed_out': True, 'interrupted': True, 'dry_run': True,
                           'report_only': True, 'truncated_output': True, 'engine': 'other-provider',
                           'task_id': 'foreign', 'contract_revision': 2, 'contract_hash': '0' * 64,
                           'source_revision': '0' * 64}.items():
            with self.subTest(field=key):
                self.record = original | {key: value}
                with self.assertRaises(ValueError):
                    self.accept()
                self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_missing_failed_truncated_or_foreign_native_finish_is_rejected(self):
        for reason in ('length', 'tool-calls', 'error'):
            with self.subTest(reason=reason):
                self.save_report(reason)
                with self.assertRaises(ValueError):
                    self.accept()
        self.save_report()
        self.events.append(self.event('step_start', 'unfinished', message='next'))
        self.save_events()
        with self.assertRaises(ValueError):
            self.accept()
        self.save_report()
        Path(self.record['events']).write_bytes(Path(self.record['events']).read_bytes().rstrip(b'\n'))
        with self.assertRaises(ValueError):
            self.accept()
        self.save_report()
        self.events[0]['sessionID'] = 'ses_builder'
        self.save_events()
        with self.assertRaises(ValueError):
            self.accept()

    def test_wrong_task_contract_cid_or_duplicate_case_is_rejected(self):
        original = deepcopy(self.report)
        for key, value in {'task_id': 'T2', 'contract_revision': 2, 'contract_hash': '0' * 64,
                           'design_manifest_hash': '0' * 64}.items():
            with self.subTest(field=key):
                self.report = original | {key: value}
                self.save_report()
                with self.assertRaises(ValueError):
                    self.accept()
        for fault in ('cid', 'duplicate', 'missing'):
            self.report = deepcopy(original)
            if fault == 'cid':
                self.report['design_results'][0]['criterion_ids'] = ['foreign']
            elif fault == 'duplicate':
                self.report['design_results'].append(deepcopy(self.results[0]))
            else:
                self.report['design_results'].pop()
            self.save_report()
            with self.assertRaises(ValueError):
                self.accept()

    def test_changed_source_reference_or_viewport_capture_is_rejected(self):
        ref = self.results[0]['capture_ref']
        original = deepcopy(self.captures[ref])
        for fault in ('source', 'reference', 'viewport', 'state', 'route'):
            self.captures[ref] = deepcopy(original)
            if fault in ('source', 'reference'):
                self.captures[ref]['source_revision' if fault == 'source' else 'reference_hash'] = '0' * 64
            elif fault == 'viewport':
                self.captures[ref]['case']['viewport']['width'] = 3
            else:
                self.captures[ref]['case'][fault] = 'wrong'
            with self.assertRaises(ValueError):
                self.accept()

    def test_wrong_bytes_at_correct_image_path_are_rejected(self):
        Path(self.results[0]['candidate_ref']).write_bytes(png((1, 1, 1)))
        with self.assertRaises(ValueError):
            self.accept()

    def test_manifest_file_bytes_are_not_the_parsed_body_hash(self):
        self.current['manifest_file_sha256'] = self.current['manifest_body_hash']
        with self.assertRaises(ValueError):
            self.accept()

    def test_modified_original_report_cannot_be_rebound_to_valid_events(self):
        Path(self.record['output']).write_text(json.dumps(self.report | {'verdict': 'FAIL'}))
        with self.assertRaises(ValueError):
            self.accept()

    def test_callback_mutation_cannot_replace_original_event_pins(self):
        original = self.delivery
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            with Path(self.record['events']).open('ab') as stream:
                stream.write(b'\n')
            return result
        with self.assertRaises(ValueError):
            self.accept(verify_delivery=mutate)
        self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_one_failed_state_blocks_frame_and_whole_task(self):
        self.results[1]['status'] = 'FAIL'
        self.report['verdict'] = 'FAIL'
        self.save_report()
        self.accept()
        result = self.summary()
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(1, result['accepted_cases'])
        self.assertEqual(0, result['accepted_frames'])
        self.assertFalse(result['current_all_accepted'])

    def test_historical_pass_not_current_after_any_identity_change(self):
        self.accept()
        original = deepcopy(self.current)
        for key, value in {'task_id': 'T2', 'contract_revision': 2, 'contract_hash': '0' * 64,
                           'source_revision': '0' * 64, 'runtime_hash': '0' * 64,
                           'manifest_file_sha256': '0' * 64,
                           'reviewer': {'provider': 'opencode', 'model': 'other/model'}}.items():
            with self.subTest(field=key):
                result = self.summary(current=original | {key: value})
                self.assertEqual(0, result['accepted_cases'])
                self.assertIsNone(result['cases'][0]['current_accepted'])
                self.assertEqual(self.record['finished_at'], result['cases'][0]['historical_accepted_at'])
        changed = deepcopy(original)
        changed['criteria'][0]['criterion'] = 'Different approved behavior'
        self.assertEqual(0, self.summary(current=changed)['accepted_cases'])

    def test_missing_reauthenticated_capture_or_changed_artifact_invalidates_current(self):
        self.accept()
        self.assertEqual(0, self.summary(verified_capture_hashes=[])['accepted_cases'])
        for key in ('events', 'output'):
            path = Path(self.record[key])
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            self.assertEqual(0, self.summary()['accepted_cases'])
            path.write_bytes(original)
        path = self.root / 'empty-context.json'
        path.write_text('{}')
        self.assertEqual(0, self.summary()['accepted_cases'])

    def test_receipt_mutation_and_model_supplied_fields_are_not_authority(self):
        self.accept()
        self.state['visual_acceptance_receipts'][0]['accepted_at'] = '2026-10-04T02:00:00+00:00'
        self.assertEqual(0, self.summary()['accepted_cases'])
        self.assertIsNone(self.summary()['cases'][0]['historical_accepted_at'])

    def test_summary_defaults_unknown_without_current_artifact_verification(self):
        self.accept()
        before = deepcopy(self.state)
        result = visual.summary(self.state['visual_acceptance_receipts'], current=self.current,
                                manifest=self.state['settings']['design_manifest'])
        self.assertEqual(0, result['accepted_cases'])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(before, self.state)

    def audit_rows(self):
        # Actual model-free native audit ordering, retained from 1.18.33's real
        # SDK -> fetch -> local HTTP response -> native message lifecycle probe.
        bound = visual.binding(self.current, self.state['settings']['design_manifest'])
        context = {'request_id': 'request-1', 'session_id': 'ses_reviewer', 'user_message_id': 'user-1',
                   'agent': 'validator', 'provider': 'opencode', 'model': self.current['reviewer']['model'],
                   'image_capable': True}
        message = {'session_id': 'ses_reviewer', 'message_id': 'msg_reviewer', 'user_message_id': 'user-1',
                   'model': context['model'], 'agent': 'validator', 'finish': None}
        images = []
        for case, result in zip(bound['cases'], self.results):
            for path in (self.root / case['artifacts']['screenshot']['path'], Path(result['candidate_ref'])):
                images.append({'sha256': util.file_hash(path), 'mime': 'image/png', 'bytes': path.stat().st_size})
        rows = [
            {'type': 'audit_start', 'version': 2, 'attempt_id': 'attempt-1',
             'binding_sha256': util.digest(bound), 'plugin_sha256': util.digest('pinned-plugin'),
             'max_requests': 1, 'reviewer': {**bound['reviewer'], 'agent': 'validator'}},
            {'type': 'native_message', **message},
            {'type': 'review_context', **context},
            {'type': 'request', **context, 'body_sha256': util.digest('actual-final-request'), 'wire_model': 'gpt-6-sol',
             'images': images, 'payload_complete': True, 'admission_index': 1},
            {'type': 'response', 'request_id': 'request-1', 'status': 200, 'response_id': 'http-response-1',
             'content_type': 'text/event-stream', 'body_present': True, 'cloneable': True, 'admission_index': 1},
            {'type': 'request_complete', 'request_id': 'request-1', 'response_id': 'model-response-1',
             'complete': True, 'finish_reason': 'stop', 'wire_format': 'sse',
             'response_bytes': 100, 'response_body_sha256': util.digest('full-response-bytes'), 'admission_index': 1},
            {'type': 'native_start', 'session_id': 'ses_reviewer', 'message_id': 'msg_reviewer',
             'part_id': 'start', 'reason': None},
            {'type': 'native_finish', 'session_id': 'ses_reviewer', 'message_id': 'msg_reviewer',
             'part_id': 'finish', 'reason': 'stop'},
            {'type': 'native_message', **message, 'finish': 'stop'},
        ]
        return [{'sequence': index, 'at_ms': 1791075600000 + index, **row} for index, row in enumerate(rows)]

    def audited_delivery(self, rows):
        path = self.run / 'native-audit.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        def verify(*args, **kwargs):
            return delivery.verify(*args, **kwargs, audit_path=path, attempt_id='attempt-1',
                                   plugin_sha256=util.digest('pinned-plugin'))
        return verify

    def test_normalized_transport_contract_drives_single_receipt(self):
        callback = self.audited_delivery(self.audit_rows())
        receipt = self.accept(verify_delivery=callback)
        self.assertEqual('model-response-1', receipt['delivery']['response_id'])
        self.assertEqual('finish', receipt['delivery']['finish_id'])
        self.assertEqual(receipt, self.accept(verify_delivery=callback))
        self.assertEqual(1, len(self.state['visual_acceptance_receipts']))
        self.assertEqual(2, self.summary()['accepted_cases'])

    def test_actual_audit_format_rejects_missing_payload_and_identity_faults(self):
        faults = [(0, 'attempt_id', 'foreign'), (0, 'plugin_sha256', '0' * 64),
                  (0, 'binding_sha256', '0' * 64), (0, 'version', 1),
                  (0, 'max_requests', 0), (0, 'max_requests', None), (0, 'max_requests', True),
                  (0, 'reviewer', {'provider': 'opencode', 'model': 'builder', 'agent': 'validator'}),
                  (1, 'user_message_id', 'foreign'), (1, 'model', 'builder/model'), (1, 'agent', 'builder'),
                  (3, 'payload_complete', False), (3, 'images', []), (3, 'image_capable', False),
                  (3, 'request_id', 'foreign'), (3, 'body_sha256', None), (3, 'model', 'builder/model'),
                  (3, 'admission_index', 2), (4, 'admission_index', 2), (5, 'admission_index', 2),
                  (4, 'status', 400), (4, 'status', 500), (4, 'body_present', False), (4, 'cloneable', False),
                  (5, 'response_bytes', 0), (5, 'response_bytes', 16 * 1024 * 1024 + 1),
                  (5, 'response_body_sha256', None), (5, 'wire_format', 'unknown'),
                  (5, 'complete', False), (5, 'finish_reason', 'length'),
                  (5, 'response_id', ''), (6, 'message_id', 'foreign'), (7, 'part_id', 'foreign'),
                  (7, 'reason', 'error'), (7, 'session_id', 'foreign')]
        for index, key, value in faults:
            with self.subTest(row=index, field=key):
                rows = self.audit_rows()
                rows[index][key] = value
                with self.assertRaises(ValueError):
                    self.accept(verify_delivery=self.audited_delivery(rows))
                self.assertNotIn('visual_acceptance_receipts', self.state)

    def test_actual_audit_format_rejects_failed_request_and_changed_bytes(self):
        for kind in ('request_failed', 'response_unverified', 'request_unverified', 'request_denied'):
            rows = self.audit_rows()
            rows.append({'sequence': len(rows), 'at_ms': 1791075600010, 'type': kind, 'request_id': 'request-1'})
            with self.assertRaises(ValueError):
                self.accept(verify_delivery=self.audited_delivery(rows))
        for fault in ('missing_image', 'wrong_bytes', 'wrong_mime', 'duplicate_image', 'mixed_request'):
            with self.subTest(fault=fault):
                rows = self.audit_rows()
                if fault == 'missing_image':
                    rows[3]['images'].pop()
                elif fault == 'wrong_bytes':
                    rows[3]['images'][0]['sha256'] = '0' * 64
                elif fault == 'wrong_mime':
                    rows[3]['images'][0]['mime'] = 'text/plain'
                elif fault == 'duplicate_image':
                    rows[3]['images'][0] = deepcopy(rows[3]['images'][1])
                else:
                    rows[5]['request_id'] = 'another-request'
                with self.assertRaises(ValueError):
                    self.accept(verify_delivery=self.audited_delivery(rows))

    def test_actual_audit_format_rejects_partial_or_ambiguous_records(self):
        for remove in (1, 2, 3, 4, 5, 6, 7):
            rows = self.audit_rows()
            rows.pop(remove)
            rows = [{**row, 'sequence': index} for index, row in enumerate(rows)]
            with self.assertRaises(ValueError):
                self.accept(verify_delivery=self.audited_delivery(rows))
        for duplicate in (0, 2, 3, 4, 5, 7):
            rows = self.audit_rows()
            rows.insert(duplicate, deepcopy(rows[duplicate]))
            rows = [{**row, 'sequence': index} for index, row in enumerate(rows)]
            with self.assertRaises(ValueError):
                self.accept(verify_delivery=self.audited_delivery(rows))
        callback = self.audited_delivery(self.audit_rows())
        path = self.run / 'native-audit.jsonl'
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        with self.assertRaises(ValueError):
            self.accept(verify_delivery=callback)

    @unittest.skipUnless(shutil.which('node'), 'Existing Node runtime needed; never install it for this test')
    def test_real_plugin_audits_final_fetch_without_logging_secrets(self):
        plugin = Path(__file__).resolve().parents[1] / 'tools' / 'autocode_image_delivery.mjs'
        script = r'''
            import { readFileSync } from 'node:fs';
            import { pathToFileURL } from 'node:url';
            const [plugin, scenario, files] = process.argv.slice(1);
            const expected = JSON.parse(files);
            let called = 0;
            const original = async (url, init) => {
              called++;
              if (url !== 'http://127.0.0.1/fake' || new Headers(init.headers).get('authorization') !== 'SECRET_SENTINEL') throw Error('Transport changed');
              if (scenario === 'fetch_failure') throw Error('SECRET_FAILURE');
              const body = JSON.parse(init.body);
              if (JSON.stringify(body.messages[0].content.map(x => x.image_url.url)) !== JSON.stringify(expected)) throw Error('Payload changed');
              const row = {id:'provider-response', object:'chat.completion.chunk', choices:[{index:0, delta:{content:'SECRET_REPLY'}, finish_reason:scenario === 'length' ? 'length' : 'stop'}]};
              let text = 'data: ' + JSON.stringify(row) + '\n\ndata: [DONE]\n\n';
              const headers = {'content-type':'text/event-stream','x-request-id':'http-id'};
              if (scenario.startsWith('json')) {
                const body = {id:'provider-response', object:'response', status:'completed', output:[{type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text:'SECRET_REPLY'}]}],error:null,incomplete_details:null};
                if (scenario === 'json_incomplete') body.status = 'incomplete';
                if (scenario === 'json_no_id') delete body.id;
                if (scenario === 'json_no_object') delete body.object;
                if (scenario === 'json_error') body.error = {message:'SECRET_ERROR'};
                if (scenario === 'json_chat' || scenario === 'json_length') {
                  body.object = 'chat.completion';
                  body.choices = [{index:0,finish_reason:scenario === 'json_length'?'length':'stop',message:{role:'assistant',content:'SECRET_REPLY'}}];
                }
                text = JSON.stringify(body);
                if (scenario === 'json_truncated') text = text.slice(0,-1);
                headers['content-type'] = 'application/json';
              }
              if (scenario === 'sse_responses') {
                text = 'event: response.completed\ndata: ' + JSON.stringify({type:'response.completed',response:{id:'provider-response',object:'response',status:'completed',output:[{type:'message',content:[{type:'output_text',text:'SECRET_REPLY'}]}]}}) + '\n\n';
              }
              if (scenario.endsWith('missing_type')) delete headers['content-type'];
              if (scenario.endsWith('wrong_type')) headers['content-type'] = 'text/plain';
              if (scenario === 'truncated') text = text.trimEnd();
              if (scenario === 'sse_no_done') text = 'data: ' + JSON.stringify(row) + '\n\n';
              if (scenario === 'sse_mixed_id') text = 'data: ' + JSON.stringify({...row,id:'other',choices:[]}) + '\n\n' + text;
              if (scenario === 'sse_error') text = 'data: ' + JSON.stringify({error:{message:'SECRET_ERROR'}}) + '\n\n' + text;
              if (scenario === 'oversize') text += ' '.repeat(16*1024*1024);
              const response = new Response(scenario === 'body_missing' ? null : Buffer.from(text), {status:scenario === 'http_failure' ? 500 : 200, headers});
              if (scenario === 'clone_failure') response.clone = () => {throw Error('SECRET_CLONE_ERROR')};
              return response;
            };
            globalThis.fetch = original;
            const { default: load } = await import(pathToFileURL(plugin));
            const hooks = await load();
            const info = {role:'assistant',sessionID:'ses_reviewer',id:'msg_reviewer',parentID:'user-1',providerID:'openai',modelID:'gpt-6-sol',agent:'validator'};
            await hooks.event({event:{type:'message.updated',properties:{info}}});
            const output = {headers:{authorization:'SECRET_SENTINEL'}};
            await hooks['chat.headers']({sessionID:'ses_reviewer',agent:'validator',message:{id:'user-1'},model:{providerID:'openai',id:'gpt-6-sol',capabilities:{input:{image:scenario !== 'unsupported'}}}},output);
            if (scenario !== 'read_only') {
              try {
                const response = await fetch('http://127.0.0.1/fake',{method:'POST',headers:output.headers,body:JSON.stringify({model:'gpt-6-sol',messages:[{role:'user',content:expected.map(url=>({type:'image_url',image_url:{url}}))}]})});
                const text = await response.text();
                if (scenario !== 'body_missing' && !text.includes('SECRET_REPLY')) throw Error('Response changed');
              } catch (error) { if (scenario !== 'fetch_failure') throw error; }
            }
            for (const [type,id] of [['step-start','start'],['step-finish','finish']]) {
              await hooks.event({event:{type:'message.part.updated',properties:{part:{type,id,sessionID:'ses_reviewer',messageID:'msg_reviewer',reason:type === 'step-finish'?'stop':undefined}}}});
            }
            await hooks.event({event:{type:'message.updated',properties:{info:{...info,finish:'stop'}}}});
            // Drain queued clone-reader microtasks without real timers.
            for (let n=0;n<30;n++) await Promise.resolve();
            if (called !== (scenario === 'read_only' ? 0 : 1)) throw Error('Unexpected request count');
        '''
        bound = visual.binding(self.current, self.state['settings']['design_manifest'])
        files = []
        for case, result in zip(bound['cases'], self.results):
            files.extend([self.root / case['artifacts']['screenshot']['path'], Path(result['candidate_ref'])])
        payloads = ['data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() for path in files]
        passing = ('happy', 'sse_missing_type', 'sse_wrong_type', 'sse_responses', 'json', 'json_chat',
                   'json_missing_type', 'json_wrong_type')
        failing = ('unsupported', 'read_only', 'http_failure', 'fetch_failure', 'length', 'truncated', 'json_incomplete',
                   'json_no_id', 'json_no_object', 'json_error', 'json_truncated', 'json_length', 'sse_no_done',
                   'sse_mixed_id', 'sse_error', 'oversize', 'body_missing', 'clone_failure')
        for scenario in (*passing, *failing):
            with self.subTest(scenario=scenario):
                path = self.run / (scenario + '-plugin-audit.jsonl')
                env = {'PATH': os.environ.get('PATH', ''), 'AUTOCODE_IMAGE_AUDIT': json.dumps({
                    'path': str(path), 'attempt_id': 'attempt-1', 'binding_sha256': util.digest(bound),
                    'max_requests': 1, 'reviewer': {**bound['reviewer'], 'agent': 'validator'}})}
                result = subprocess.run(['node', '--input-type=module', '-e', script, str(plugin), scenario,
                                         json.dumps(payloads)], env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(0, result.returncode, result.stderr)
                raw = path.read_bytes()
                self.assertNotIn(b'SECRET', raw)
                self.assertNotIn(b'base64,', raw)
                def verifier(*args, **kwargs):
                    return delivery.verify(*args, **kwargs, audit_path=path, attempt_id='attempt-1',
                                           plugin_sha256=util.file_hash(plugin))
                response_rows = [json.loads(line) for line in raw.splitlines() if json.loads(line)['type'] == 'response']
                if response_rows:
                    self.assertIn('content_type', response_rows[0])
                    self.assertIn('body_present', response_rows[0])
                    self.assertIn('cloneable', response_rows[0])
                if scenario in passing:
                    self.assertEqual('provider-response', self.accept(verify_delivery=verifier)['delivery']['response_id'])
                    self.assertEqual(2, self.summary()['accepted_cases'])
                    self.state.pop('visual_acceptance_receipts')
                else:
                    with self.assertRaises(ValueError):
                        self.accept(verify_delivery=verifier)
                    self.assertNotIn('visual_acceptance_receipts', self.state)

    @unittest.skipUnless(shutil.which('node'), 'Existing Node runtime needed')
    def test_http_admission_caps_retries_and_rejects_unattributed_inference(self):
        plugin = Path(__file__).resolve().parents[1] / 'tools' / 'autocode_image_delivery.mjs'
        observed = []
        auth = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/oauth/token':
                    auth.append(raw)
                    payload = b'{"access_token":"fixture-not-a-secret"}'
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                observed.append(json.loads(raw))
                chunk = {'id': 'provider-response', 'object': 'chat.completion.chunk',
                         'choices': [{'index': 0, 'delta': {'content': 'answer'}, 'finish_reason': 'stop'}]}
                payload = ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        script = r'''
            import { pathToFileURL } from 'node:url';
            const [plugin, endpoint, scenario] = process.argv.slice(1);
            const {default: load} = await import(pathToFileURL(plugin));
            const hooks = await load();
            if (scenario === 'oauth') {
              const response = await fetch(endpoint.replace('/chat/completions','/oauth/token'),
                {method:'POST',body:'grant_type=refresh_token&refresh_token=SECRET_REFRESH'});
              if (!(await response.text()).includes('fixture-not-a-secret')) process.exit(99);
            }
            const input = {sessionID:'ses_reviewer',agent:'validator',message:{id:'user-1'},model:{providerID:'openai',id:'gpt-6-sol',capabilities:{input:{image:true}}}};
            let output = {headers:{}};
            await hooks['chat.headers'](input, output);
            const body = JSON.stringify({model:'gpt-6-sol',messages:[{role:'user',content:'fixture'}]});
            const send = async (headers=output.headers,url=endpoint) => {
              const response = await fetch(url,{method:'POST',headers,body});
              await response.text();
            };
            await send();
            if (scenario === 'cap_two') await send();
            if (scenario === 'new_nonce') { output={headers:{}}; await hooks['chat.headers'](input, output); }
            if (scenario === 'foreign_agent') await hooks['chat.headers']({...input,agent:'builder'}, {headers:{}});
            if (scenario === 'foreign_model') await hooks['chat.headers']({...input,model:{...input.model,id:'builder'}}, {headers:{}});
            if (scenario === 'foreign_session') await hooks['chat.headers']({...input,sessionID:'ses_foreign'}, {headers:{}});
            if (scenario === 'missing_nonce') await send({});
            else if (scenario === 'custom_route') await send({},endpoint.replace('/chat/completions','/custom'));
            else if (scenario === 'unknown_nonce') await send({'x-autocode-image-request':'SECRET_UNKNOWN_NONCE'});
            else await send();
            // A third fetch must be unreachable; exiting via an SDK exception is insufficient.
            await send();
            process.exitCode = 99;
        '''
        bound = visual.binding(self.current, self.state['settings']['design_manifest'])
        for scenario in ('same_nonce', 'new_nonce', 'missing_nonce', 'custom_route', 'unknown_nonce',
                         'foreign_agent', 'foreign_model', 'foreign_session', 'default_cap', 'cap_two', 'oauth'):
            with self.subTest(scenario=scenario):
                observed.clear()
                auth.clear()
                path = self.run / (scenario + '-admission.jsonl')
                options = {'path': str(path), 'attempt_id': 'attempt-1', 'binding_sha256': util.digest(bound),
                           'reviewer': {**bound['reviewer'], 'agent': 'validator'}}
                cap = 2 if scenario == 'cap_two' else 1
                if scenario != 'default_cap':
                    options['max_requests'] = cap
                result = subprocess.run(['node', '--input-type=module', '-e', script, str(plugin),
                                         f'http://127.0.0.1:{server.server_port}/chat/completions', scenario],
                                        env={'PATH': os.environ.get('PATH', ''), 'AUTOCODE_IMAGE_AUDIT': json.dumps(options)},
                                        capture_output=True, timeout=15)
                self.assertEqual(77, result.returncode, result.stderr)
                self.assertEqual(cap, len(observed))
                self.assertEqual([b'grant_type=refresh_token&refresh_token=SECRET_REFRESH'] if scenario == 'oauth' else [], auth)
                raw = path.read_bytes()
                self.assertNotIn(b'SECRET', raw)
                rows = [json.loads(line) for line in raw.splitlines()]
                self.assertEqual(cap, rows[0]['max_requests'])
                self.assertEqual(list(range(1, cap + 1)), [row['admission_index'] for row in rows if row['type'] == 'request'])
                denials = [row for row in rows if row['type'] == 'request_denied']
                self.assertEqual(1, len(denials))
                self.assertEqual(cap, denials[0]['admitted_requests'])
                with self.assertRaisesRegex(ValueError, 'admission denied'):
                    delivery.verify(self.record, events=Path(self.record['events']).read_bytes(), report=b'{}', images=[],
                                    binding=bound, audit_path=path, attempt_id='attempt-1', plugin_sha256=util.file_hash(plugin))
                self.assertNotIn('visual_acceptance_receipts', self.state)

    @unittest.skipUnless(shutil.which('node'), 'Existing Node runtime needed')
    def test_invalid_admission_configuration_exits_before_any_fetch(self):
        plugin = Path(__file__).resolve().parents[1] / 'tools' / 'autocode_image_delivery.mjs'
        script = r'''
            import { pathToFileURL } from 'node:url';
            globalThis.fetch=()=>{process.exit(99)};
            const {default:load}=await import(pathToFileURL(process.argv[1]));
            await load();
            process.exitCode=98;
        '''
        for index, value in enumerate((0, -1, None, True, 1.5, '1', 2**53)):
            options = {'path': str(self.run / ('invalid-' + str(index) + '.jsonl')), 'attempt_id': 'fixture',
                       'binding_sha256': 'a' * 64, 'max_requests': value,
                       'reviewer': {'provider': 'opencode', 'model': 'openai/gpt-6-sol', 'agent': 'validator'}}
            result = subprocess.run(['node', '--input-type=module', '-e', script, str(plugin)],
                env={'PATH': os.environ.get('PATH', ''), 'AUTOCODE_IMAGE_AUDIT': json.dumps(options)},
                capture_output=True, timeout=10)
            self.assertEqual(78, result.returncode)
            self.assertFalse(Path(options['path']).exists())


if __name__ == '__main__':
    unittest.main()
