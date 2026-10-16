"""Actual Linux controls for the existing native Kilo boundary; no model calls."""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import autocode_kilo_linux_boundary as boundary
import autocode_kilo_linux_peer as peers
import autocode_process as processes


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_NATIVE_LINUX_BOUNDARY_TEST") == "1",
    "requires the explicit disposable Linux kernel fixture",
)
class KiloLinuxBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="kilo-linux-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.target = self.root / "target"
        self.scratch = self.root / "scratch"
        self.target.mkdir()
        self.scratch.mkdir()

    def prepare(self, *, kind="derivation", reads=(), hidden=()):
        profile = self.target / (kind + ".json")
        value = boundary.policy(
            profile,
            kind=kind,
            read_paths=[self.target, *reads],
            write_paths=[self.scratch],
            hidden_paths=hidden,
            cwd=self.target,
        )
        boundary.write(profile, value)
        return profile

    def run_target(self, profile, code):
        return subprocess.run(
            [*boundary.prefix(profile), str(Path(sys.executable).resolve()), "-I", "-S", "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_pure_allows_threads_but_denies_process_fork_and_network(self):
        profile = self.prepare(reads=[Path(sys.executable).resolve().parents[1]])
        code = """import os,socket,threading
seen=[]
t=threading.Thread(target=lambda:seen.append(1));t.start();t.join();assert seen==[1]
try: os.fork()
except PermissionError: pass
else: raise AssertionError('process fork escaped')
s=socket.socket()
try: s.connect(('127.0.0.1',1))
except PermissionError: pass
else: raise AssertionError('new network escaped')
print('PURE_BOUNDARY_PASS')"""
        result = self.run_target(profile, code)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PURE_BOUNDARY_PASS", result.stdout)

    def test_unix_relay_is_reachable_while_foreign_socket_and_host_network_are_absent(self):
        visible = self.root / "relay.sock"
        foreign = self.root / "foreign.sock"
        server = socket.socket(socket.AF_UNIX)
        other = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        self.addCleanup(other.close)
        server.bind(str(visible))
        server.listen(1)
        other.bind(str(foreign))
        other.listen(1)
        received = []

        def respond():
            server.settimeout(10)
            with server.accept()[0] as client:
                received.append(client.recv(16))
                client.sendall(b"original")

        worker = threading.Thread(target=respond)
        worker.start()
        profile = self.prepare(kind="client", reads=[visible, Path(sys.executable).resolve().parents[1]])
        code = f"""import socket
s=socket.socket(socket.AF_UNIX);s.connect({str(visible)!r});s.sendall(b'actual');assert s.recv(16)==b'original';s.close()
s=socket.socket(socket.AF_UNIX)
try:s.connect({str(foreign)!r})
except FileNotFoundError:pass
else:raise AssertionError('foreign socket exposed')
try:
 s=socket.socket();s.connect(('1.1.1.1',443))
except PermissionError:pass
else:raise AssertionError('Internet exposed')
print('UNIX_BOUNDARY_PASS')"""
        try:
            result = self.run_target(profile, code)
        finally:
            worker.join(timeout=11)
        self.assertFalse(worker.is_alive())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([b"actual"], received)

    def test_original_namespace_pid1_is_bound_but_its_forked_peer_is_refused(self):
        visible = self.root / "owner.sock"
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(str(visible))
        server.listen(2)
        server.settimeout(10)
        profile = self.prepare(kind="client", reads=[visible, Path(sys.executable).resolve().parents[1]])
        code = f"""import os,socket
s=socket.socket(socket.AF_UNIX);s.connect({str(visible)!r});s.sendall(b'root');assert s.recv(2)==b'OK';s.close()
pid=os.fork()
if pid==0:
 s=socket.socket(socket.AF_UNIX);s.connect({str(visible)!r});s.sendall(b'foreign');assert s.recv(2)==b'NO';s.close();os._exit(0)
_,status=os.waitpid(pid,0);assert status==0
"""
        child = subprocess.Popen(
            [*boundary.prefix(profile), str(Path(sys.executable).resolve()), "-I", "-S", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        tree = processes.ProcessTree(child.pid, lambda _rows: None)
        anchor = tree.capture_root()
        self.assertIsNotNone(anchor)
        try:
            for label, response in ((b"root", b"OK"), (b"foreign", b"NO")):
                with server.accept()[0] as connection:
                    connection.settimeout(5)
                    pid, uid, _ = __import__("struct").unpack(
                        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    self.assertEqual(os.getuid(), uid)
                    self.assertEqual(label, connection.recv(16))
                    peer = processes.identity(processes.process_table({pid})[pid])
                    self.assertEqual(
                        Path(sys.executable).resolve(), Path(processes.psutil.Process(pid).exe()).resolve()
                    )
                    if label == b"root":
                        observation = peers.observe(peer, anchor, tree)
                        peers.validate_retained(observation, peer=peer, anchor=anchor)
                        self.assertEqual(1, peers.reported_pid(peer))
                    else:
                        with self.assertRaises(ValueError):
                            peers.observe(peer, anchor, tree)
                        self.assertEqual(2, peers.reported_pid(peer))
                    connection.sendall(response)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(0, child.returncode, stderr.decode())
        finally:
            tree.stop(child)
            child.wait(timeout=5)

    def test_policy_filter_and_command_tampering_refuse(self):
        profile = self.prepare()
        command = [*boundary.prefix(profile), "/usr/bin/true"]
        self.assertEqual(["/usr/bin/true"], boundary.unwrap(command, profile))
        with self.assertRaises(ValueError):
            boundary.unwrap(["/usr/bin/true"], profile)
        program = profile.with_suffix(".bpf")
        program.chmod(0o600)
        program.write_bytes(b"changed")
        program.chmod(0o400)
        with self.assertRaises(ValueError):
            boundary.prefix(profile)

    def test_original_compiled_linux_kilo_version_under_whole_client_policy(self):
        executable = Path(os.environ["AUTOCODE_KILO_LINUX_COMPILED_CLIENT"])
        profile = self.prepare(kind="client", reads=[executable.parent])
        environment = dict(os.environ)
        for key, name, parent in (
            ("HOME", "home", self.target),
            ("XDG_CONFIG_HOME", "config", self.target),
            ("XDG_DATA_HOME", "data", self.scratch),
            ("XDG_CACHE_HOME", "cache", self.scratch),
            ("XDG_STATE_HOME", "state", self.scratch),
            ("TMPDIR", "tmp", self.scratch),
        ):
            directory = parent / name
            directory.mkdir()
            environment[key] = str(directory)
        (self.target / "config/kilo").mkdir()
        environment.update(KILO_DISABLE_MODELS_FETCH="1", KILO_DISABLE_PROJECT_CONFIG="1")
        result = subprocess.run(
            [*boundary.prefix(profile), str(executable), "--version"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("7.8.3-issue1063-source", result.stdout.strip())
