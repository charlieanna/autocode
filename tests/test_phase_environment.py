"""Application root bindings and swallowed default-transport refusal controls."""
import asyncio
import contextlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scenarios'))
from harness.phase_env import ERROR, GREEN, PhaseSequence

CLIENT = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ['PHASE_HARNESS_ROOT'])
from harness.phase_env import guard, RefusedTransportError
path = Path(os.environ['CLAUDE_CONFIG_DIR']) / '.credentials.json'
if sys.argv[1] == 'stats':
    path.write_text(json.dumps({'token': 'synthetic-stats-only'}))
elif path.exists():
    try: guard().get('https://usage.synthetic.invalid/v1/usage')
    except RefusedTransportError: pass
print(json.dumps({'credentials_found': path.exists(), 'home': os.environ.get('HOME')}))
'''

class ApplicationPhaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def sequence(self, name, **kwargs):
        return PhaseSequence(name, self.root / name, **kwargs)

    def test_real_environment_variable_is_bound_and_parent_is_unchanged(self):
        ambient = self.root / 'ambient'; ambient.mkdir()
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(ambient)}):
            before = dict(os.environ)
            sequence = self.sequence('bound')
            bindings = {'CLAUDE_CONFIG_DIR': 'credential_root', 'XDG_CACHE_HOME': 'cache_root'}
            stats = sequence.phase('stats', root_vars=bindings)
            compat = sequence.phase('compat', root_vars=bindings)
            stats.run([sys.executable, '-B', '-c', CLIENT, 'stats'])
            child = compat.run([sys.executable, '-B', '-c', CLIENT, 'compat'])
            self.assertEqual(0, child.returncode, child.stderr)
            self.assertFalse(json.loads(child.stdout)['credentials_found'])
            self.assertEqual(before.get('HOME'), json.loads(child.stdout)['home'])
            record = sequence.finish()
            self.assertEqual(GREEN, record['outcome'], record)
            self.assertEqual(str(stats.credential_root), record['phases'][0]['environment_roots']['CLAUDE_CONFIG_DIR'])
            self.assertEqual([], record['phases'][1]['unexpected_requests'])
            self.assertEqual(before, dict(os.environ))
            self.assertFalse((ambient / '.credentials.json').exists())

    def test_shared_application_credentials_remain_error_despite_green_child(self):
        sequence = self.sequence('shared')
        bindings = {'CLAUDE_CONFIG_DIR': 'credential_root'}
        stats = sequence.phase('stats', root_vars=bindings)
        compat = sequence.phase('compat', root_vars=bindings, share_credential_root_with=stats)
        stats.run([sys.executable, '-B', '-c', CLIENT, 'stats'])
        child = compat.run([sys.executable, '-B', '-c', CLIENT, 'compat'])
        self.assertEqual(0, child.returncode, child.stderr)
        record = sequence.finish()
        self.assertEqual(ERROR, record['outcome'])
        self.assertEqual(1, len(record['phases'][1]['unexpected_requests']))

    def test_explicit_environment_drops_ambient_credentials_without_mutation(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_OAUTH_TOKEN': 'synthetic-parent-token'}):
            before = dict(os.environ)
            prepared = {key: value for key, value in before.items() if key != 'CLAUDE_CODE_OAUTH_TOKEN'}
            sequence = self.sequence('prepared', env=prepared)
            prepared['CLAUDE_CODE_OAUTH_TOKEN'] = 'late-mutation'
            phase = sequence.phase('compat')
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', phase.build_env())
            self.assertEqual(before, dict(os.environ))
            self.assertEqual(before.get('HOME'), phase.build_env().get('HOME'))

    def test_bindings_cannot_override_home_or_escape_owned_roots(self):
        for index, bindings in enumerate(({'HOME': 'state_root'}, {'PATH': 'state_root'},
                {'PHASE_NAME': 'state_root'}, {'CLAUDE_CONFIG_DIR': 'missing'}, {'bad=name': 'state_root'}, {'CLAUDE_CONFIG_DIR': []})):
            with self.subTest(bindings=bindings):
                sequence = self.sequence('invalid-' + str(index))
                with self.assertRaises(ValueError):
                    sequence.phase('compat', root_vars=bindings)
        sequence = self.sequence('fixed')
        phase = sequence.phase('compat', root_vars={'CLAUDE_CONFIG_DIR': 'credential_root'})
        with self.assertRaises(ValueError):
            phase.build_env(CLAUDE_CONFIG_DIR=str(self.root / 'unowned'))
        with self.assertRaises(ValueError):
            self.sequence('wrong-home', env={**os.environ, 'HOME': str(self.root / 'other-home')})

    def test_sibling_phases_keep_declared_inputs_after_mapping_mutation(self):
        sequence = self.sequence('frozen-binding')
        bindings = {'CLAUDE_CONFIG_DIR': 'credential_root'}
        phase = sequence.phase('compat', root_vars=bindings)
        bindings['CLAUDE_CONFIG_DIR'] = 'state_root'
        self.assertEqual(str(phase.credential_root), phase.build_env()['CLAUDE_CONFIG_DIR'])

    def test_default_httpx_guard_records_sync_and_async_swallowed_refusals(self):
        from harness.phase_httpx import guard_default_httpx
        phase = self.sequence('transports').phase('compat')
        calls = []
        class Sync:
            def handle_request(self, request):
                calls.append('sync')
                raise AssertionError('unadmitted transport reached')
        class Async:
            async def handle_async_request(self, request):
                calls.append('async')
                raise AssertionError('unadmitted transport reached')
        module = SimpleNamespace(HTTPTransport=Sync, AsyncHTTPTransport=Async)
        original = (Sync.handle_request, Async.handle_async_request)
        request = SimpleNamespace(method='GET', url='https://usage.synthetic.invalid/v1/usage')
        with guard_default_httpx(phase.build_env(), httpx_module=module):
            for execute in (lambda: Sync().handle_request(request),
                            lambda: asyncio.run(Async().handle_async_request(request))):
                with contextlib.suppress(RuntimeError):
                    execute()  # the production client may swallow a refusal
        self.assertEqual([], calls)
        self.assertEqual(original, (Sync.handle_request, Async.handle_async_request))
        record = phase.sequence.finish()
        self.assertEqual(ERROR, record['outcome'])
        self.assertEqual(['GET', 'GET'], [row['method'] for row in record['phases'][0]['unexpected_requests']])

    def test_frozen_sdk_inputs_cannot_follow_external_symlinks(self):
        from headroom_phase_check import source_inventory
        package = self.root / 'headroom'; package.mkdir()
        (package / '_core.abi3.so').write_bytes(b'declared-sdk')
        external = self.root / 'unowned.py'; external.write_text('not a declared input')
        (package / 'client.py').symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            source_inventory(self.root)

    def test_nonloopback_dns_and_literal_connections_are_refused_before_io(self):
        from harness.phase_env import RefusedTransportError
        from harness.phase_sockets import guard_connections
        phase = self.sequence('egress').phase('compat')
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS must not run')) as dns:
            with guard_connections(phase.build_env()):
                with self.assertRaises(RefusedTransportError):
                    socket.getaddrinfo('usage.synthetic.invalid', 443)
                with self.assertRaises(RefusedTransportError):
                    socket.getaddrinfo('usage.synthetic.invalid', 'https')
                with socket.socket() as connection:
                    for connect in (connection.connect, connection.connect_ex):
                        with self.assertRaises(RefusedTransportError):
                            connect(('192.0.2.10', 443))
            dns.assert_not_called()
        self.assertEqual(4, len(phase.unexpected_requests()))
        self.assertEqual(ERROR, phase.sequence.finish()['outcome'])

    def test_declared_loopback_connection_and_dns_reach_real_socket(self):
        from harness.phase_sockets import guard_connections
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1)
            port = listener.getsockname()[1]
            phase = self.sequence('loopback').phase('stats', allowed_endpoints=(f'127.0.0.1:{port}',))
            original = socket.socket.connect
            with guard_connections(phase.build_env()):
                with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
                    accepted, address = listener.accept()
                    with accepted:
                        client.sendall(b'ok'); self.assertEqual(b'ok', accepted.recv(2))
            self.assertIs(original, socket.socket.connect)
            self.assertEqual(GREEN, phase.sequence.finish()['outcome'])

    def test_admitted_transport_still_executes_and_restores_after_exception(self):
        from harness.phase_httpx import guard_default_httpx
        phase = self.sequence('admitted').phase('stats', allowed_endpoints=('127.0.0.1:8765',))
        calls = []
        class Sync:
            def handle_request(self, request):
                calls.append(str(request.url)); return 'real-response'
        class Async:
            async def handle_async_request(self, request):
                calls.append(str(request.url)); return 'async-response'
        module = SimpleNamespace(HTTPTransport=Sync, AsyncHTTPTransport=Async)
        original = Sync.handle_request
        request = SimpleNamespace(method='GET', url='http://127.0.0.1:8765/stats')
        with self.assertRaisesRegex(ValueError, 'application failure'):
            with guard_default_httpx(phase.build_env(), httpx_module=module):
                self.assertEqual('real-response', Sync().handle_request(request))
                self.assertEqual('async-response', asyncio.run(Async().handle_async_request(request)))
                raise ValueError('application failure')
        self.assertIs(original, Sync.handle_request)
        self.assertEqual(2, len(calls))
        self.assertEqual(GREEN, phase.sequence.finish()['outcome'])

if __name__ == '__main__':
    unittest.main()
