"""Actual owned process death/restart observations, independent of catalog holdouts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_risk_acceptance as acceptance
import autocode_risk_protocols as protocols
import autocode_risk_targets as targets

CATALOG = Path(__file__).resolve().parents[1] / 'scenarios' / 'catalog'
FIXTURES = {
    'lease_queue_lifecycle_v1': ('ladder-18-durable-lease-queue', 'leasequeue', 'LeaseQueue',
        ['enqueue', 'claim', 'ack', 'nack', 'pending'], 'process-local-tokens'),
    'transactional_outbox_lifecycle_v1': ('ladder-19-transactional-outbox', 'outbox', 'Store',
        ['create_order', 'orders', 'pending', 'publish'], 'ack-with-exception-rollback'),
}


def observation(protocol):
    scenario, module, class_name, methods, _ = FIXTURES[protocol]
    text = (CATALOG / scenario / 'brief.md').read_text()
    value = {'protocol': protocol, 'target': {'module': module, 'class_name': class_name,
             'methods': {name: name for name in methods}}, 'criterion_ids': ['AC-lifecycle'],
             'source_bindings': [{'source_id': 'task', 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                                  'span': [0, len(text)], 'quote': text}]}
    value['hash'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                            ensure_ascii=False).encode()).hexdigest()
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def execute(protocol, variant='reference', extra_files=None, timeout=30, *, layout='root', original_inventory=False):
    scenario, module, _, _, _ = FIXTURES[protocol]
    case = observation(protocol)
    with tempfile.TemporaryDirectory(prefix='risk-protocol-test-') as directory:
        root = Path(directory)
        base = root if layout == 'root' else root / 'src'
        base.mkdir(exist_ok=True)
        if original_inventory:
            shutil.copytree(CATALOG / scenario / 'seed' / module, base / module)
            shutil.copytree(CATALOG / scenario / 'seed' / 'tests', root / 'tests')
            artifact = targets.verify(targets.capture(root))
            expected_path = ('' if layout == 'root' else 'src/') + module + '/__init__.py'
            if len(artifact['targets']) != 1 or artifact['targets'][0]['path'] != expected_path:
                raise AssertionError('Original public seed did not capture the requested import layout')
            sources = [{'id': 'task:0', 'kind': 'task', 'text': (CATALOG / scenario / 'brief.md').read_text()}]
            declarations = acceptance.inventory(sources, artifact['targets'])
            manifest = acceptance.bind(sources, [{'declaration_id': row['id'], 'criterion_ids': ['AC-lifecycle'],
                'module': module} for row in declarations], public_targets=artifact['targets'])
            acceptance.verify(sources, manifest, public_targets=artifact['targets'])
            if len(manifest['observations']) != 1:
                raise AssertionError('Original brief did not bind exactly one lifecycle observation')
            case = manifest['observations'][0]
            shutil.rmtree(base / module)
        shutil.copytree(CATALOG / scenario / variant / module, base / module)
        for name, contents in (extra_files or {}).items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(contents, Path):
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink(missing_ok=True)
                destination.symlink_to(contents, target_is_directory=contents.is_dir())
            else:
                destination.write_text(contents)
        before = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in root.rglob('*.py')}
        command = protocols.commands([case], python=sys.executable, timeout=timeout)[0]
        process = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            stdout, stderr = process.communicate(timeout=35)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise
        after = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in root.rglob('*.py')}
        if before != after:
            raise AssertionError('Protocol modified the source-hashed candidate fixture')
        if list(root.glob('.risk-protocol-*')):
            raise AssertionError('Protocol left owned runtime data in the candidate')
        return process.returncode, stdout, stderr, case


class ActualRiskProtocolTests(unittest.TestCase):
    def test_queue_reference_passes_and_process_local_tokens_fail_after_actual_hard_kill(self):
        protocol = 'lease_queue_lifecycle_v1'
        for variant, passing in [('reference', True), ('broken/process-local-tokens', False)]:
            with self.subTest(variant=variant):
                code, raw, stderr, case = execute(protocol, variant)
                self.assertEqual(b'', stderr)
                actual = json.loads(raw)
                self.assertEqual(-signal.SIGKILL, actual['phases'][0]['worker']['exit_code'])
                self.assertEqual(passing, code == 0, actual.get('error'))
                if passing:
                    checked = protocols.validate_transcript(raw, case)
                    self.assertNotEqual(checked['phases'][0]['calls']['claim']['token'],
                                        checked['phases'][1]['calls']['claim']['token'])
                    self.assertTrue(checked['phases'][2]['calls']['ack'])
                else:
                    self.assertIn('lease token', actual['error'])
                    forged = {**actual, 'verdict': 'PASS', 'error': ''}
                    with self.assertRaises(ValueError):
                        protocols.validate_transcript(encoded(forged), case)

    def test_outbox_reference_retains_duplicate_delivery_and_ack_before_sink_mutant_loses_middle(self):
        protocol = 'transactional_outbox_lifecycle_v1'
        for variant, passing in [('reference', True), ('broken/ack-with-exception-rollback', False)]:
            with self.subTest(variant=variant):
                code, raw, stderr, case = execute(protocol, variant)
                self.assertEqual(b'', stderr)
                actual = json.loads(raw)
                self.assertEqual(-signal.SIGKILL, actual['phases'][1]['worker']['exit_code'])
                self.assertEqual(passing, code == 0, actual.get('error'))
                if passing:
                    checked = protocols.validate_transcript(raw, case)
                    delivered = [row['event']['event_id'] for row in checked['sink']['deliveries']]
                    self.assertEqual(4, len(delivered))
                    self.assertEqual(delivered[1], delivered[2])
                    self.assertEqual(3, len(set(delivered)))
                else:
                    self.assertIn('unacknowledged event', actual['error'])
                    forged = {**actual, 'verdict': 'PASS', 'error': ''}
                    with self.assertRaises(ValueError):
                        protocols.validate_transcript(encoded(forged), case)

    def test_src_reference_protocols_pass_with_original_public_target_inventory(self):
        for protocol in FIXTURES:
            with self.subTest(protocol=protocol):
                code, raw, stderr, case = execute(protocol, layout='src', original_inventory=True)
                self.assertEqual(b'', stderr)
                self.assertEqual(0, code, json.loads(raw)['error'])
                actual = protocols.validate_transcript(raw, case)
                crash = 0 if protocol.startswith('lease_') else 1
                self.assertEqual(-signal.SIGKILL, actual['phases'][crash]['worker']['exit_code'])
                self.assertTrue(all(worker['reaped'] for worker in actual['owned_workers']))

    def test_root_src_and_module_package_ambiguities_reject_before_import(self):
        scenario, module, _, _, _ = FIXTURES['lease_queue_lifecycle_v1']
        source = (CATALOG / scenario / 'reference' / module / '__init__.py').read_text()
        with tempfile.TemporaryDirectory(prefix='risk-ambiguous-target-') as directory:
            marker = Path(directory) / 'executed'
            hook = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
            for duplicate in ['src/leasequeue/__init__.py', 'leasequeue.py']:
                with self.subTest(duplicate=duplicate):
                    code, raw, stderr, case = execute('lease_queue_lifecycle_v1',
                        extra_files={'leasequeue/__init__.py': hook + source, duplicate: hook + source})
                    self.assertEqual(b'', stderr)
                    self.assertEqual(1, code)
                    self.assertFalse(marker.exists(), 'Target ambiguity must be rejected before either import')
                    self.assertIn('ambiguous', json.loads(raw)['error'])

    def test_src_target_symlinks_reject_before_external_code_executes(self):
        with tempfile.TemporaryDirectory(prefix='risk-src-outside-target-') as directory:
            outside = Path(directory)
            package = outside / 'leasequeue'
            package.mkdir()
            marker, module = outside / 'executed', package / '__init__.py'
            module.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
                              "raise RuntimeError('External src module executed')\n")
            links = [('src', outside), ('src/leasequeue', package), ('src/leasequeue/__init__.py', module)]
            for name, destination in links:
                with self.subTest(name=name):
                    code, raw, stderr, case = execute('lease_queue_lifecycle_v1', layout='src',
                        original_inventory=True, extra_files={name: destination})
                    self.assertEqual(b'', stderr)
                    self.assertEqual(1, code)
                    self.assertFalse(marker.exists(), 'Target containment must precede external src execution')
                    self.assertIn('escapes candidate', json.loads(raw)['error'])

    def test_candidate_stdlib_shadow_cannot_replace_isolated_supervisor(self):
        shadow = "raise RuntimeError('Candidate shadow imported by trusted supervisor')\n"
        for protocol in FIXTURES:
            with self.subTest(protocol=protocol):
                code, raw, stderr, case = execute(protocol, extra_files={'base64.py': shadow, 'selectors.py': shadow})
                self.assertEqual(0, code, stderr.decode())
                self.assertEqual('PASS', protocols.validate_transcript(raw, case)['verdict'])

    def _assert_owned_worker_failure(self, body, reason, timeout):
        source = 'class LeaseQueue:\n    def __init__(self,path):\n        ' + body + '\n'
        code, raw, stderr, case = execute('lease_queue_lifecycle_v1',
            extra_files={'leasequeue/__init__.py': source}, timeout=timeout)
        self.assertEqual(1, code, stderr.decode())
        actual = json.loads(raw)
        self.assertIn(reason, actual['error'])
        self.assertLessEqual(actual['elapsed_seconds'], timeout)
        self.assertEqual(1, len(actual['owned_workers']))
        owned = actual['owned_workers'][0]
        self.assertTrue(owned['reaped'])
        self.assertEqual(-signal.SIGKILL, owned['exit_code'])
        with self.assertRaises(ProcessLookupError): os.kill(owned['pid'], 0)
        with self.assertRaises(ValueError): protocols.validate_transcript(raw, case)

    def test_shared_deadline_still_kills_and_reaps_owned_worker(self):
        self._assert_owned_worker_failure('while True: pass', 'deadline', timeout=1)

    def test_output_bound_still_kills_and_reaps_owned_worker(self):
        # Use the normal protocol budget so this case must reach the byte bound;
        # the separate deadline case retains its one-second bound.
        self._assert_owned_worker_failure("while True: print('x' * 65536, flush=True)",
            'Candidate output exceeded shared bound', timeout=30)

    def test_target_symlink_is_rejected_before_external_module_executes(self):
        with tempfile.TemporaryDirectory(prefix='risk-outside-target-') as directory:
            outside = Path(directory)
            marker, module = outside / 'executed', outside / 'outside.py'
            module.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n'
                              "raise RuntimeError('External module executed')\n")
            code, raw, stderr, case = execute('lease_queue_lifecycle_v1',
                extra_files={'leasequeue/__init__.py': module})
            self.assertEqual(1, code)
            self.assertFalse(marker.exists(), 'Target containment must precede module execution')
            self.assertIn('escapes candidate', json.loads(raw)['error'])


class TranscriptControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual = {}
        for protocol in FIXTURES:
            code, raw, stderr, case = execute(protocol)
            if code or stderr:
                raise AssertionError((code, stderr, raw))
            cls.actual[protocol] = (json.loads(raw), case)

    def reject(self, protocol, mutate):
        original, case = self.actual[protocol]
        changed = copy.deepcopy(original)
        mutate(changed)
        with self.assertRaises(ValueError):
            protocols.validate_transcript(encoded(changed), case)

    def test_actual_negative_exit_identity_and_phase_chronology_are_required(self):
        for protocol in FIXTURES:
            crash = 0 if protocol.startswith('lease_') else 1
            changes = [lambda d: d['phases'].reverse(),
                       lambda d: d['phases'][crash]['worker'].update(exit_code=0),
                       lambda d: d['phases'][crash]['worker'].update(exit_code=True),
                       lambda d: d['phases'][1]['worker'].update(pid=d['phases'][0]['worker']['pid']),
                       lambda d: d['phases'][0]['worker'].update(parent_pid=d['supervisor_pid'] + 1),
                       lambda d: d['phases'][crash]['worker'].update(terminated=d['phases'][crash]['worker']['received']),
                       lambda d: d['phases'][2]['worker'].update(started=d['phases'][1]['worker']['received']),
                       lambda d: d['owned_workers'][0].update(reaped=False),
                       lambda d: d.update(elapsed_seconds=d['deadline_seconds'] + 1),
                       lambda d: d['phases'].pop()]
            for index, change in enumerate(changes):
                with self.subTest(protocol=protocol, change=index):
                    self.reject(protocol, change)

    def test_public_lifecycle_values_and_durable_sink_are_rechecked(self):
        queue = 'lease_queue_lifecycle_v1'
        changes = [lambda d: d['phases'][1]['calls'].update(stale_ack=True),
                   lambda d: d['phases'][1]['calls']['claim'].update(token=d['phases'][0]['calls']['claim']['token']),
                   lambda d: d['phases'][1]['calls']['claim'].update(deadline=26),
                   lambda d: d['phases'][2]['calls'].update(ack=False),
                   lambda d: d['phases'][2]['calls']['other'].update(id='risk-job-a')]
        for index, change in enumerate(changes):
            with self.subTest(protocol=queue, change=index): self.reject(queue, change)
        outbox = 'transactional_outbox_lifecycle_v1'
        changes = [lambda d: d['phases'][2]['calls']['before']['pending'].pop(0),
                   lambda d: d['phases'][2]['calls']['before']['pending'][0].update(event_id='replacement'),
                   lambda d: d['phases'][0]['calls']['orders'].update({'risk-order-a':37.0}),
                   lambda d: d['phases'][2]['calls']['after'].update(counts=[2,0,0]),
                   lambda d: d['sink']['deliveries'].pop(1),
                   lambda d: d['sink']['deliveries'][2].update(worker_pid=d['phases'][1]['worker']['pid']),
                   lambda d: d['sink'].update(journal_sha256='0' * 64)]
        for index, change in enumerate(changes):
            with self.subTest(protocol=outbox, change=index): self.reject(outbox, change)

    def test_partial_duplicate_or_unbounded_json_is_not_a_transcript(self):
        original, case = self.actual['lease_queue_lifecycle_v1']
        raw = encoded(original)
        for invalid in [raw[:-1], raw + b' extra', b'{"version":1,"version":1}',
                        b'x' * (protocols.MAX_TRANSCRIPT_BYTES + 1)]:
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                protocols.validate_transcript(invalid, case)

    def test_observation_source_or_target_tampering_cannot_replay_actual_receipt(self):
        original, case = self.actual['lease_queue_lifecycle_v1']
        for key in ['source', 'target']:
            changed = copy.deepcopy(case)
            if key == 'source': changed['source_bindings'][0]['source_sha256'] = '0' * 64
            else: changed['target']['module'] = 'another'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'hash changed'):
                protocols.validate_transcript(encoded(original), changed)
            changed['hash'] = hashlib.sha256(json.dumps({k: v for k,v in changed.items() if k != 'hash'},
                sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            with self.subTest(key=key, rehashed=True), self.assertRaisesRegex(ValueError, 'another observation'):
                protocols.validate_transcript(encoded(original), changed)


class CompilerBounds(unittest.TestCase):
    def test_only_fixed_protocols_identifiers_and_bounded_caller_budget_compile(self):
        case = observation('lease_queue_lifecycle_v1')
        self.assertEqual([], protocols.commands([]))
        for python in ['sh', 'python3; echo bad', '../python3', 'python3\n']:
            with self.subTest(python=python), self.assertRaises(ValueError):
                protocols.commands([case], python=python)
        for timeout in [True, 0, -1, 31, float('nan')]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                protocols.commands([case], timeout=timeout)
        for field, value in [('script', 'raise SystemExit(0)'), ('expected', {'PASS': True})]:
            changed = {**case, field: value}
            changed['hash'] = hashlib.sha256(encoded({k: v for k, v in changed.items() if k != 'hash'})).hexdigest()
            with self.subTest(field=field), self.assertRaises(ValueError): protocols.commands([changed])
        with self.assertRaises(ValueError): protocols.commands([case, case])


if __name__ == '__main__':
    unittest.main()
