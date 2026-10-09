"""Public CLI supervisor-loss controls using an explicitly offline provider."""
import json
import os
import pty
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_process as processes
import autocode_supervision_cli as bridge

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


class DeclarationTests(unittest.TestCase):
    def packet(self):
        return {'schema': 1, 'nonce': 'a' * 32, 'owner': {'pid': os.getppid(), 'birth_identity': 123},
                'deadline': 110, 'timeout_seconds': 20, 'receipt': '/tmp/owned-supervision.json'}

    def read(self, data):
        r, w = os.pipe()
        try:
            os.write(w, data)
            with patch.object(bridge.time, 'monotonic', return_value=100):
                return bridge.declaration(r)
        finally:
            os.close(r)
            os.close(w)

    def test_absolute_remaining_deadline_never_extends_the_harness_cap(self):
        packet, remaining = self.read(json.dumps(self.packet()).encode() + b'\n')
        self.assertEqual(10, remaining)
        self.assertEqual(self.packet(), packet)

    def test_invalid_owner_deadline_nonce_or_receipt_refuses_admission(self):
        for change in ({'schema': 2}, {'nonce': 'x' * 32}, {'receipt': 'relative'},
                       {'owner': {'pid': os.getpid(), 'birth_identity': 123}},
                       {'deadline': 99}, {'timeout_seconds': True}, {'deadline': float('nan')}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.read(json.dumps({**self.packet(), **change}).encode() + b'\n')

    def test_closed_writer_and_oversized_declaration_refuse_before_launch(self):
        r, w = os.pipe()
        os.close(w)
        try:
            with self.assertRaisesRegex(ValueError, 'disappeared'):
                bridge.declaration(r)
        finally:
            os.close(r)
        with self.assertRaisesRegex(ValueError, 'exceeded'):
            self.read(b'x' * (bridge.MAX_DECLARATION_BYTES + 1) + b'\n')

    def test_no_lifeline_keeps_the_existing_argv(self):
        with bridge.guard(['--status', '--run-dir', '/tmp/run']) as argv:
            self.assertEqual(['--status', '--run-dir', '/tmp/run'], argv)

    def test_hangup_handler_respects_an_inherited_ignore(self):
        old = signal.getsignal(signal.SIGHUP)
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            with processes.interruption_handler():
                self.assertEqual(signal.SIG_IGN, signal.getsignal(signal.SIGHUP))
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            with processes.interruption_handler():
                with self.assertRaises(KeyboardInterrupt):
                    signal.raise_signal(signal.SIGHUP)
            self.assertEqual(signal.SIG_DFL, signal.getsignal(signal.SIGHUP))
        finally:
            signal.signal(signal.SIGHUP, old)


@unittest.skipUnless(os.name == 'posix', 'POSIX owner/session fault controls')
class SupervisorLossCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ac454-', dir='/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        subprocess.run(['git', '-C', str(self.workspace), '-c', 'user.name=Fixture',
                        '-c', 'user.email=f@example.test', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        self.run = None
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        shutil.copy2(TOOLS / 'goal_fixtures.py', self.bin / 'goal_fixtures.py')
        source = (TOOLS / 'fake_codex.py').read_text()
        # Complete events intentionally precede provider exit: loss must retain
        # this response without treating it as a successful recovered stage.
        barrier = '''import socket
_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
_socket.connect(os.environ['AUTOCODE_FIXTURE_SUPERVISION_SOCKET'])
with open(os.environ['AUTOCODE_FIXTURE_SUPERVISION_LAUNCHES'], 'a') as _log:
    _log.write(str(os.getpid()) + '\\n')
Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps({
    'workflow': 'build', 'reason': 'Offline fault fixture', 'signals': [],
    'design_document': '', 'clarity': 'vague'}))
print(json.dumps({'type': 'thread.started', 'thread_id': 'fault-session'}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}), flush=True)
if os.environ.get('AUTOCODE_FIXTURE_SUPERVISION_HOLD_TERM'):
    import signal
    def _hold(signum, frame):
        # Report the controller's stop, then end once the test has acted on it.
        _socket.sendall(b'T')
        _socket.recv(1)
        os._exit(128 + signum)
    signal.signal(signal.SIGTERM, _hold)
_socket.sendall(str(os.getpid()).encode() + b'\\n')
_socket.recv(1)
raise SystemExit(0)
'''
        source = source.replace('stage = data["stage"]', barrier + '\nstage = data["stage"]', 1)
        (self.bin / 'codex').write_text(source)
        (self.bin / 'codex').chmod(0o755)
        self.address = str(self.root / 'ready.sock')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.address)
        self.listener.listen(1)
        self.listener.settimeout(25)
        self.addCleanup(self.listener.close)
        self.launches = self.root / 'launches'
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'AUTOCODE_HOME': str(self.root / 'registry'), 'CODEX_HOME': str(self.root / 'codex-home'),
                    'XDG_CONFIG_HOME': str(self.root / 'config'), 'PYTHONDONTWRITEBYTECODE': '1',
                    'AUTOCODE_FIXTURE_SUPERVISION_SOCKET': self.address,
                    'AUTOCODE_FIXTURE_SUPERVISION_LAUNCHES': str(self.launches)}
        self.env.pop('AUTOCODE_PROVIDER', None)
        self.entry = [sys.executable, str(TOOLS / 'autocode.py')]
        self.command = [*self.entry, 'Build a greeting CLI', '--engine', 'codex',
                        '--workspace', str(self.workspace), '--in-place', '--no-chat',
                        '--max-stage-seconds', '60']
        self.sentinel = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                         stdin=subprocess.PIPE, start_new_session=True)
        self.addCleanup(self.stop_child, self.sentinel)

    @staticmethod
    def stop_child(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=8)
        if child.stdin:
            child.stdin.close()

    def inspect(self, *args):
        return subprocess.run([*self.entry, '--workspace', str(self.workspace),
                               *(['--run-dir', str(self.run)] if self.run else []), *args],
                              env=self.env, capture_output=True, text=True, timeout=25)

    def fault(self, kind):
        master = None
        slave = None
        if kind == 'pty':
            master, slave = pty.openpty()
            # Establish a controlling terminal before exec, rather than merely
            # attaching a non-controlling PTY to an already detached session.
            launcher = ('import fcntl,os,sys,termios; os.setsid(); '
                        'fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(sys.argv[1],sys.argv[1:])')
            child = subprocess.Popen([sys.executable, '-c', launcher, *self.command], env=self.env,
                                     stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            slave = None
        else:
            output = (self.root / 'cli.log').open('w')
            self.addCleanup(output.close)
            child = subprocess.Popen(self.command, env=self.env, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=output, start_new_session=True)
        self.addCleanup(self.stop_child, child)
        try:
            try:
                connection, _ = self.listener.accept()
            except TimeoutError as error:
                logs = self.root / 'cli.log'
                raise AssertionError(f'CLI exit={child.poll()}; provider did not reach its barrier; '
                                     + (logs.read_text() if logs.exists() else 'PTY output unavailable')) from error
            with connection:
                connection.settimeout(8)
                ready = bytearray()
                while not ready.endswith(b'\n'):
                    ready.extend(connection.recv(64))
                provider_pid = int(ready)
                running = self.inspect('--status')
                self.assertEqual(0, running.returncode, running.stderr)
                running_status = json.loads(running.stdout)
                self.run = Path(running_status['run_dir'])
                self.assertEqual('supervised', running_status['view']['liveness']['kind'])
                if kind == 'kill':
                    child.kill()
                elif kind == 'group':
                    os.killpg(child.pid, signal.SIGKILL)
                elif kind == 'hup':
                    os.kill(child.pid, signal.SIGHUP)
                elif kind == 'later-hups':
                    os.kill(child.pid, signal.SIGHUP)
                    self.assertEqual(b'T', connection.recv(1), 'The controller stops its provider on a hangup')
                    # A closed terminal's second SIGHUP (the shell's) lands while it does.
                    os.kill(child.pid, signal.SIGHUP)
                    connection.sendall(b'A')
                else:
                    os.close(master)
                    master = None
                self.assertEqual(b'', connection.recv(1), 'Provider must stop after owner/session loss')
            if kind == 'later-hups':
                self.hang_up_while_the_pause_is_saved(child)
            child.wait(timeout=12)
            self.assertIsNone(self.sentinel.poll(), 'Unrelated process must remain alive')
            original = (self.run / 'state.json').read_bytes()
            status = self.inspect('--status')
            self.assertEqual(0, status.returncode, status.stderr)
            public = json.loads(status.stdout)
            self.assertEqual(original, (self.run / 'state.json').read_bytes(), 'Status must stay read-only')
            self.assertFalse(public['view']['liveness']['provider']['alive'])
            if kind in ('hup', 'later-hups', 'pty'):
                # A hung-up terminal failed the pause's own output with EIO: a traceback and exit 1 (#454).
                self.assertEqual((2, 'PAUSED_INTERRUPTED'), (child.returncode, public['status']))
                self.assertIsNotNone(public['active_stage']['exit_code'],
                                     'The controller collects its provider before the interrupt propagates')
            if public['status'] == 'RUNNING':
                self.assertTrue(public['stale'])
            launches = self.launches.read_bytes()
            resumed = self.inspect()
            self.assertEqual(2, resumed.returncode, resumed.stderr)
            self.assertIn('lacks a verified uninterrupted result', resumed.stderr)
            self.assertIn('--abandon-stage', resumed.stderr)
            held = self.inspect('--status')
            self.assertEqual('WAITING_FOR_USER', json.loads(held.stdout)['status'])
            self.assertEqual(launches, self.launches.read_bytes(), 'Resume must not replay an interrupted provider')
            self.assertEqual([provider_pid], [int(p) for p in launches.splitlines()])
        finally:
            if master is not None:
                os.close(master)
            if slave is not None:
                os.close(slave)

    def test_parent_sigkill_retains_late_terminal_response_without_adoption(self):
        self.fault('kill')

    def test_cli_process_group_sigkill_cleans_the_separate_provider_session(self):
        self.fault('group')

    def test_hangup_retains_interruption_and_stops_provider(self):
        self.fault('hup')

    def hang_up_while_the_pause_is_saved(self, child):
        """Send one more SIGHUP once the stage's record is final, before the CLI has exited."""
        deadline = time.monotonic() + 12
        while child.poll() is None and time.monotonic() < deadline:
            if 'exit_code' in json.loads((self.run / 'state.json').read_text()).get('active_stage', {}):
                os.kill(child.pid, signal.SIGHUP)
                return
            time.sleep(.001)

    def test_later_hangups_leave_the_cleanup_and_the_pause_to_the_controller(self):
        # A closed terminal sends SIGHUP twice (the kernel and the shell). Raised again while the
        # controller stopped its provider, it cut that cleanup short; once the stage's own handler
        # scope had closed, it killed the controller before the pause was saved (#454). The
        # one-interrupt latch inside that scope is covered in test_process.
        self.env['AUTOCODE_FIXTURE_SUPERVISION_HOLD_TERM'] = '1'
        self.fault('later-hups')

    def test_controlling_pty_close_stops_provider(self):
        self.fault('pty')
