"""Pure policy checks and real macOS kernel/tool boundary conformance."""
import hashlib
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode_tool_containment as containment
from providers import opencode


ROOT = Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    def test_deny_default_has_no_whole_home_tmp_or_network_grant(self):
        profile = containment.policy('/workspace', '/workspace/.autocode/stage/scratch')
        self.assertIn('(deny default)', profile)
        self.assertNotIn('(allow default)', profile)
        self.assertNotIn('(subpath "/tmp")', profile)
        self.assertNotIn('(allow network', profile)
        self.assertNotIn('(subpath "/Users")', profile)
        self.assertIn('(literal "/")', profile)

    def test_builder_excludes_runner_and_config_authority(self):
        profile = containment.policy('/workspace', '/workspace/.autocode/stage/scratch',
                                     allow_write=True, protected_paths=['/workspace/runtime'])
        for path in ('.autocode', '.git', '.opencode', 'opencode.json', 'runtime'):
            self.assertIn('(require-not (subpath "/workspace/' + path + '"))', profile)

    def test_unscoped_read_or_write_authority_rejected(self):
        for scratch in ('/tmp/escape', '/workspace/.autocode'):
            with self.assertRaises(ValueError):
                containment.policy('/workspace', scratch)
        for root in ('/', str(Path.home())):
            with self.assertRaises(ValueError):
                containment.policy('/workspace', '/workspace/.autocode/stage/scratch', read_roots=[root])

    def test_unsupported_platform_is_explicit_prelaunch_failure(self):
        with patch.object(containment.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(RuntimeError, 'requires macOS'):
                containment.prepare('/workspace')

    def test_effective_shell_patterns_and_stricter_actions_are_preserved(self):
        rules = [{'permission': '*', 'pattern': '*', 'action': 'allow'},
                 {'permission': 'bash', 'pattern': 'git *', 'action': 'ask'},
                 {'permission': 'bash', 'pattern': 'git push*', 'action': 'deny'},
                 {'permission': 'read', 'pattern': '*', 'action': 'deny'}]
        result = containment.shell_permissions(rules)
        self.assertEqual({'*': 'allow', 'git *': 'ask', 'git push*': 'deny'}, result['bash'])
        self.assertEqual('deny', result['*'])
        self.assertEqual('deny', result['external_directory'])
        rules += [{'permission': '*', 'pattern': '*', 'action': 'deny'}]
        self.assertEqual(['git *', 'git push*', '*'], list(containment.shell_permissions(rules)['bash']))
        for unsupported in (None, [], [{'permission': 'bash', 'pattern': '*', 'action': 'auto'}]):
            with self.assertRaises(RuntimeError):
                containment.shell_permissions(unsupported)

    def test_adapter_opt_in_retains_model_effort_and_command(self):
        with patch.object(opencode.tool_containment, 'configure') as configure:
            configure.side_effect = lambda command, env, workspace, **kw: (
                {**env, 'AUTOCODE_TOOL_CONTAINMENT': '{"qualified":true}'}, {'shell': '/owned/shell'})
            command, env, overrides = opencode.launch('sol', '/workspace', '/run', None,
                'test/model', 'high', False, env={'PATH': '/bin'}, containment={'read_roots': ['/runtime']})
        self.assertEqual('test/model', command[command.index('--model') + 1])
        self.assertEqual('high', command[command.index('--variant') + 1])
        self.assertNotIn('--auto', command)
        self.assertEqual('/owned/shell', overrides['shell'])
        self.assertEqual({'read_roots': ['/runtime']}, configure.call_args.kwargs['request'])
        self.assertFalse(configure.call_args.kwargs['allow_write'])
        self.assertIn('AUTOCODE_TOOL_CONTAINMENT', env)

    def test_loopback_requests_fail_before_files_or_native_debug_are_created(self):
        authority = {'purpose': 'approved-readonly-unittest', 'binding_sha256': 'a' * 64}
        command = shlex.join([sys.executable, '-m', 'unittest', '-v', 'test_http'])
        with patch.object(containment.sys, 'platform', 'darwin'), \
                patch.object(containment.subprocess, 'run') as launch, \
                patch.object(Path, 'mkdir') as mkdir:
            for writable in (False, True):
                for request in ({'loopback_checks': [command]}, {'loopback_authority': authority},
                                {'loopback_authority': {}},
                                {'loopback_checks': [command], 'loopback_authority': authority}):
                    with self.subTest(writable=writable, request=request):
                        with self.assertRaisesRegex(RuntimeError, 'Exact-IP loopback containment is unsupported'):
                            containment.prepare('/workspace', allow_write=writable, **request)
                        with self.assertRaisesRegex(RuntimeError, 'runner-mediated approved check'):
                            containment.configure(['opencode'], {}, '/workspace', allow_write=writable,
                                request=request)
            launch.assert_not_called()
            mkdir.assert_not_called()
        containment._reject_loopback_request([], None)
        with self.assertRaises(ValueError):
            containment._reject_loopback_request(command, None)


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(),
                     'requires real macOS Seatbelt enforcement')
class KernelTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / '.autocode'
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='containment-tests-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.workspace = self.home / 'project'
        self.workspace.mkdir()
        self.source = self.workspace / 'source.py'
        self.source.write_text('VALUE = 42\n')
        self.outside = self.home / 'forbidden.txt'
        self.outside.write_text('forbidden sentinel\n')
        self.runtime = self.workspace / 'runtime'
        self.runtime.mkdir()
        self.code = self.runtime / 'runner.py'
        self.code.write_text('runner authority\n')
        private = self.workspace / '.autocode'
        private.mkdir()
        self.state = private / 'state.json'
        self.state.write_text('{"owned":"runner"}\n')
        self.events = private / 'live.jsonl'
        self.events.write_text('{"type":"step_start"}\n')
        self.receipt = private / 'accepted.json'
        self.receipt.write_text('{"accepted":true}\n')
        self.roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), ROOT / 'tools']
        self.env = {**os.environ, 'PATH': str(Path(sys.executable).parent) + ':/usr/bin:/bin:/opt/homebrew/bin'}
        self.before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (self.source, self.outside, self.code, self.state, self.events, self.receipt)}
        self.boundary = containment.prepare(self.workspace, read_roots=self.roots,
            protected_paths=[self.runtime], environment=self.env)

    def run_shell(self, command, boundary=None):
        result = subprocess.run([(boundary or self.boundary)['shell'], '-c', command],
            cwd=self.workspace, capture_output=True, text=True, timeout=20)
        return result

    def assert_denied(self, command, boundary=None):
        result = self.run_shell(command, boundary)
        self.assertNotEqual(0, result.returncode, (command, result.stdout, result.stderr))
        self.assertRegex(result.stderr, 'Operation not permitted|PermissionError|Permission denied')

    def test_external_and_readonly_writes_are_kernel_denied(self):
        escape = self.home / 'outside-created'
        commands = [shlex.join(['mkdir', str(escape)]),
                    shlex.join(['cp', str(self.source), str(self.outside)]),
                    'python -c ' + shlex.quote('from pathlib import Path; Path(' + repr(str(self.outside)) + ').write_text("bad")'),
                    ': > ' + shlex.quote(str(self.outside)),
                    'true && (: > ' + shlex.quote(str(self.source)) + ')']
        for path in (self.state, self.events, self.receipt, self.code):
            commands.append(': > ' + shlex.quote(str(path)))
        for command in commands:
            with self.subTest(command=command):
                self.assert_denied(command)
        self.assertFalse(escape.exists())
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_symlink_escape_denied_even_from_writable_scratch(self):
        link = Path(self.boundary['scratch']) / 'escape'
        link.symlink_to(self.outside)
        self.assert_denied(': > ' + shlex.quote(str(link)))
        self.assertEqual(self.before[self.outside], hashlib.sha256(self.outside.read_bytes()).hexdigest())

    def test_legitimate_reads_tests_and_stage_capture_work_without_client_secrets(self):
        test = self.workspace / 'test_source.py'
        test.write_text('import unittest\nfrom source import VALUE\n'
                        'class SourceTest(unittest.TestCase):\n'
                        '    def test_value(self): self.assertEqual(VALUE, 42)\n')
        result = self.run_shell('python -m unittest -v test_source')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('Ran 1 test', result.stderr)
        output = Path(self.boundary['scratch']) / 'capture.json'
        command = 'python -c ' + shlex.quote(
            'import json,os,pathlib,sys; '
            'assert pathlib.Path("source.py").read_text() == "VALUE = 42\\n"; '
            'assert "CONTAINMENT_TEST_SECRET" not in os.environ; '
            'assert os.environ.get("PYTHONNOUSERSITE") == "1" and sys.flags.no_user_site == 1; '
            'pathlib.Path(' + repr(str(output)) + ').write_text(json.dumps({"exit_code": 0}))')
        with patch.dict(os.environ, {'CONTAINMENT_TEST_SECRET': 'must-not-reach-tool'}):
            result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'exit_code': 0}, json.loads(output.read_text()))
        self.assertFalse((self.workspace / '__pycache__').exists())

    def test_public_capture_cli_uses_only_owned_stage_scratch(self):
        output = Path(self.boundary['scratch']) / 'approved-capture.json'
        command = shlex.join(['python', '-m', 'autocode_cli', 'capture', '--output', str(output),
                              '--', 'python', '-c', 'print("actual captured command")'])
        result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(output.read_text())
        self.assertEqual(0, receipt['exit_code'])
        self.assertEqual('actual captured command\n', Path(receipt['full_output']).read_text())
        self.assertTrue(Path(receipt['full_output']).is_relative_to(self.boundary['scratch']))

    def test_builder_may_edit_app_not_state_runtime_or_other_stage(self):
        writer = containment.prepare(self.workspace, allow_write=True, read_roots=self.roots,
            protected_paths=[self.runtime], environment=self.env)
        result = self.run_shell('python -c ' + shlex.quote('from pathlib import Path; Path("source.py").write_text("VALUE = 43\\n")'), writer)
        self.assertEqual(0, result.returncode, result.stderr)
        for path in (self.state, self.events, self.receipt, self.code,
                     Path(self.boundary['scratch']) / 'other-stage.json'):
            with self.subTest(path=path):
                self.assert_denied(': > ' + shlex.quote(str(path)), writer)

    def test_outside_read_and_network_are_denied(self):
        self.assert_denied('python -c ' + shlex.quote('from pathlib import Path; print(Path(' + repr(str(self.outside)) + ').read_text())'))
        self.assert_denied('python -c ' + shlex.quote('import socket; socket.socket().connect(("127.0.0.1", 9))'))

    def test_hardlink_cannot_turn_runner_evidence_into_writable_scratch(self):
        link = Path(self.boundary['scratch']) / 'linked-state'
        command = shlex.join(['ln', str(self.state), str(link)]) + ' && : > ' + shlex.quote(str(link))
        self.assert_denied(command)
        self.assertEqual(self.before[self.state], hashlib.sha256(self.state.read_bytes()).hexdigest())

    def test_wrapper_ignores_poisoned_shell_startup_before_sandbox(self):
        marker = self.home / 'startup-escaped'
        startup = self.home / 'poison.sh'
        startup.write_text(': > ' + shlex.quote(str(marker)) + '\n')
        env = {**os.environ, 'BASH_ENV': str(startup), 'ENV': str(startup),
               'SHELLOPTS': 'xtrace', 'PS4': '$(: > ' + shlex.quote(str(marker)) + ')'}
        result = subprocess.run([self.boundary['shell'], '-c', 'exit 0'], cwd=self.workspace,
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())

    def test_manifest_verification_rejects_changed_files_and_symlink_ancestors(self):
        control = Path(self.boundary['profile']).parent
        proof = control / 'conformance.json'
        proof.write_text('{}')  # Integrity-only fixture, not native qualification.
        boundary = {**self.boundary, 'native_version': containment.SUPPORTED_VERSION,
                    'conformance': str(proof)}
        for name in ('profile', 'shell', 'conformance'):
            boundary[name + '_sha256'] = hashlib.sha256(Path(boundary[name]).read_bytes()).hexdigest()
        containment.verify(boundary)
        for key, value in (('loopback_checks', ['claimed network permission']), ('loopback_checks', []),
                           ('loopback_authority', None), ('loopback_profile', '')):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(RuntimeError, 'authority changed'):
                    containment.verify({**boundary, key: value})
        proof.write_text('{"changed":true}')
        with self.assertRaisesRegex(RuntimeError, 'authority changed'):
            containment.verify(boundary)
        proof.write_text('{}')
        old = control.with_name(control.name + '-moved')
        control.rename(old)
        control.symlink_to(old, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'authority changed'):
            containment.verify(boundary)


@unittest.skipUnless(sys.platform == 'darwin' and Path('/usr/bin/sandbox-exec').is_file(),
                     'requires real macOS Seatbelt enforcement')
class LoopbackLimitTests(unittest.TestCase):
    setUp = KernelTests.setUp
    run_shell = KernelTests.run_shell

    def test_default_policy_denies_ephemeral_http_listener(self):
        (self.workspace / 'test_http.py').write_text(
            'import http.server, unittest\n'
            'class HTTP(unittest.TestCase):\n'
            ' def test_listener(self):\n'
            '  with http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler): pass\n')
        result = self.run_shell(shlex.join([sys.executable, '-m', 'unittest', '-v', 'test_http']))
        self.assertEqual(1, result.returncode)
        self.assertIn('PermissionError: [Errno 1] Operation not permitted', result.stderr)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_seatbelt_localhost_is_not_an_exact_loopback_ip_filter(self):
        import ipaddress
        import psutil
        addresses = [a.address for values in psutil.net_if_addrs().values() for a in values
                     if a.family == socket.AF_INET and not ipaddress.ip_address(a.address).is_loopback]
        if not addresses:
            self.skipTest('No owned non-loopback interface available to test Seatbelt address matching')
        with socket.socket() as listener:
            listener.bind((addresses[0], 0))
            listener.listen()
            listener.settimeout(2)
            code = ('import socket; s=socket.socket(); s.settimeout(1); s.connect('
                    + repr(listener.getsockname()) + '); print("connected-owned-nonloopback")')
            denied = self.run_shell(shlex.join([sys.executable, '-c', code]))
            self.assertEqual(1, denied.returncode)
            self.assertIn('Operation not permitted', denied.stderr)
            profile = containment.policy(self.workspace, self.boundary['scratch'], read_roots=self.roots)
            # This is a rejected candidate profile, NOT a production capability.
            candidate = profile + '(allow network-outbound (remote tcp "localhost:*"))\n'
            result = subprocess.run(['/usr/bin/sandbox-exec', '-p', candidate,
                                     sys.executable, '-B', '-c', code], cwd=self.workspace,
                                    env={'PYTHONNOUSERSITE': '1'}, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual('connected-owned-nonloopback\n', result.stdout)
            accepted, _ = listener.accept()
            accepted.close()

    def test_literal_loopback_ip_is_not_supported_by_sbpl_network_filter(self):
        for host in ('127.0.0.1:*', '[::1]:*'):
            with self.subTest(host=host):
                profile = containment.policy(self.workspace, self.boundary['scratch'], read_roots=self.roots)
                profile += '(allow network-outbound (remote tcp ' + json.dumps(host) + '))'
                result = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile, '/usr/bin/true'],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('host must be * or localhost', result.stderr)


@unittest.skipUnless(os.environ.get('AUTOCODE_NATIVE_CONTAINMENT_TEST') == '1',
                     'opt-in model-free OpenCode debug conformance')
class NativeToolTests(KernelTests):
    def setUp(self):
        super().setUp()
        from autocode_preflight_worker import execute
        self.execute = execute
        command, env, _ = opencode.launch('investigate_stuck', self.workspace,
            self.workspace / '.autocode' / 'run', None, 'zai-coding-plan/glm-5.3', 'high', False,
            env=self.env, containment={'read_roots': [str(p) for p in self.roots],
                                       'protected_paths': [str(self.runtime)]})
        self.worker = {'command': command, 'environment': env, 'engine': 'opencode',
                       'configured': False, 'model': 'zai-coding-plan/glm-5.3'}
        self.boundary = json.loads(env['AUTOCODE_TOOL_CONTAINMENT'])
        self.counter = 0

    def test_final_boundary_in_one_native_call(self):
        """Small retained native gate, including shell/env startup hardening."""
        targets = [self.source, self.outside, self.state, self.events, self.receipt, self.code]
        escape = Path(self.boundary['scratch']) / 'escape-final'
        escape.symlink_to(self.outside)
        programs = [['mkdir', str(self.home / 'outside-final')],
                    ['cp', str(self.source), str(self.outside)],
                    *[['python', '-c', 'from pathlib import Path; Path(' + repr(str(p)) + ').write_text("bad")']
                      for p in [*targets, escape]],
                    ['bash', '-c', 'true && (: > ' + shlex.quote(str(self.outside)) + ')']]
        test = self.workspace / 'test_source.py'
        test.write_text('import unittest\nfrom source import VALUE\n'
                        'class SourceTest(unittest.TestCase):\n'
                        '    def test_value(self): self.assertEqual(VALUE, 42)\n')
        capture = Path(self.boundary['scratch']) / 'final-capture.json'
        code = ('import subprocess,json,pathlib; results=[]\n'
                'for argv in ' + repr(programs) + ':\n'
                ' r=subprocess.run(argv,capture_output=True,text=True)\n'
                ' assert r.returncode != 0 and "Operation not permitted" in r.stderr, (argv,r)\n'
                ' results.append({"argv":argv,"exit":r.returncode,"stderr":r.stderr})\n'
                'subprocess.run(["python","-m","unittest","-v","test_source"],check=True)\n'
                'subprocess.run(' + repr(['python', '-m', 'autocode_cli', 'capture', '--output', str(capture),
                                        '--', 'python', '-c', 'print("approved final capture")']) + ',check=True)\n'
                'print(json.dumps(results))\n')
        result = self.run_shell('python -c ' + shlex.quote(code))
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})
        self.assertEqual(0, json.loads(capture.read_text())['exit_code'])
        if os.environ.get('AUTOCODE_CONTAINMENT_RETAIN_PROOF') == '1':
            destination = ROOT / '.autocode' / ('native-containment-proof-' + Path(self.boundary['profile']).parent.name)
            destination.mkdir()
            # Copy only this runner-owned fixture's proof, never auth/config data.
            import shutil
            shutil.copytree(self.home, destination / 'fixture')
            (destination / 'result.json').write_text(json.dumps({
                'native_version': containment.SUPPORTED_VERSION, 'passed': True,
                'command': result.args, 'output': result.stdout,
                'source_and_forbidden_hashes_unchanged': True,
                'before': {str(p): value for p, value in self.before.items()},
                'runtime_sha256': hashlib.sha256(Path(containment.__file__).read_bytes()).hexdigest()}, indent=2))
            print('RETAINED_NATIVE_PROOF=' + str(destination))

    def run_shell(self, command, boundary=None):
        if boundary is not None:
            # Builder's kernel policy has separate direct-process coverage.
            return super().run_shell(command, boundary)
        self.counter += 1
        output = self.home / ('native-' + str(self.counter) + '.json')
        argv = ['bash', '-c', command]
        result, text = self.execute(self.worker, argv, self.workspace, output, timeout=60)
        raw = json.loads(output.read_text())
        self.assertEqual(shlex.join(argv), raw['input']['command'])
        self.assertIs(type(raw['result']['metadata']['exit']), int)
        print(json.dumps({'native_command': shlex.join(argv), 'exit': result['exit_code'],
                          'output': text}, sort_keys=True))
        return subprocess.CompletedProcess(argv, result['exit_code'], text, text)


if __name__ == '__main__':
    unittest.main()
