"""Native Linux boundary controls; kernel outcomes are not image acceptance."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_native_linux_boundary as boundary


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_NATIVE_LINUX_BOUNDARY_TEST") == "1",
    "explicit supported Linux kernel qualification required",
)
class NativeLinuxKernelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-linux-boundary-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.private = self.source / ".autocode"
        self.private.mkdir()
        (self.private / "secret").write_text("runner-authority")
        self.writable = self.private / "scratch"
        self.writable.mkdir()
        self.outside = self.root / "outside"
        self.outside.write_text("unapproved-private-bytes")
        self.file = self.source / "app.py"
        self.file.write_text("original source")
        self.filter = boundary.seccomp_filter(self.root / "filter.bpf")

    def execute(self, script, *, inherited=()):
        with self.filter.open("rb") as program:
            args = boundary.arguments(
                read_paths=tuple(
                    dict.fromkeys((self.source, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()))
                ),
                write_paths=(self.writable,),
                hidden_paths=(self.private,),
                cwd=self.source,
                seccomp_fd=program.fileno(),
                inherited_fds=inherited,
            )
            return subprocess.run(
                [str(boundary.executable()), *args, "--", sys.executable, "-I", "-c", script],
                env={"PATH": "/usr/bin:/bin", "HOME": str(self.writable), "PYTHONDONTWRITEBYTECODE": "1"},
                pass_fds=(program.fileno(), *inherited),
                capture_output=True,
                text=True,
                timeout=20,
            )

    def test_actual_readonly_source_hidden_authority_writable_scratch_and_network_denials(self):
        script = f"""
import errno,json,socket
from pathlib import Path
source=Path({str(self.file)!r}); scratch=Path({str(self.writable)!r})
assert source.read_text() == 'original source'
scratch.joinpath('result').write_text('owned scratch')
for operation in (
    lambda:source.write_text('corrupted'),
    lambda:Path({str(self.outside)!r}).read_text(),
    lambda:Path({str(self.private / "secret")!r}).read_text(),
    lambda:socket.socket().bind(('127.0.0.1',0)),
    lambda:socket.socket().connect(('127.0.0.1',9)),
):
    try: operation()
    except OSError: pass
    else: raise AssertionError('kernel accepted forbidden operation')
print(json.dumps({{'readonly':True,'authority_hidden':True,'scratch':True,'new_network_denied':True}}))
"""
        result = self.execute(script)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("original source", self.file.read_text())
        self.assertEqual("owned scratch", (self.writable / "result").read_text())
        self.assertEqual("runner-authority", (self.private / "secret").read_text())
        self.assertTrue(all(json.loads(result.stdout).values()))

    def test_actual_inherited_listener_serves_while_new_connect_and_bind_are_denied(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with socket.create_connection(listener.getsockname(), timeout=3) as client:
                client.sendall(b"original-inherited-channel")
                script = f"""
import socket
original=socket.socket(fileno={listener.fileno()})
original.settimeout(3)
connection,_=original.accept()
with connection:
    assert connection.recv(128)==b'original-inherited-channel'
    connection.sendall(b'original-response')
for operation in (
    lambda:socket.socket().bind(('127.0.0.1',0)),
    lambda:socket.socket().connect(('127.0.0.1',{listener.getsockname()[1]})),
):
    try: operation()
    except PermissionError: pass
    else: raise AssertionError('new socket authority escaped filter')
print('ORIGINAL_INHERITED_SOCKET_ONLY')
"""
                result = self.execute(script, inherited=(listener.fileno(),))
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(b"original-response", client.recv(128))
                self.assertEqual("ORIGINAL_INHERITED_SOCKET_ONLY", result.stdout.strip())

    def test_alias_shared_write_inode_and_widened_authority_refuse_before_launch(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        hardlink = self.root / "shared-app"
        hardlink.hardlink_to(self.file)
        with self.filter.open("rb") as program:
            common = {"cwd": self.source, "seccomp_fd": program.fileno()}
            for values in (
                {"read_paths": (alias,)},
                {"read_paths": (Path("/"),)},
                {"read_paths": (self.source,), "write_paths": (self.file,)},
                {"read_paths": (self.source,), "write_paths": (self.source,)},
                {"read_paths": (self.source,), "inherited_fds": (program.fileno(),)},
            ):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    boundary.arguments(**common, **values)
        self.assertEqual("original source", self.file.read_text())

    def test_adoption_relisten_is_noop_and_reusing_the_fd_cannot_create_a_listener(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            self.filter = boundary.seccomp_filter(self.root / "adoption.bpf", inherited_listener=listener.fileno())
            with socket.create_connection(listener.getsockname(), timeout=3) as client:
                client.sendall(b"actual-original-listener")
                script = f"""
import os,socket
slot={listener.fileno()}
original=socket.socket(fileno=slot)
original.listen(128)
assert original.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==1
original.settimeout(3)
connection,_=original.accept()
with connection:
    assert connection.recv(128)==b'actual-original-listener'
    connection.sendall(b'actual-original-response')
original.close()
replacement=socket.socket()
if replacement.fileno()!=slot:
    current=replacement.detach();os.dup2(current,slot);os.close(current)
    replacement=socket.socket(fileno=slot)
replacement.listen(128)
assert replacement.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)==0
assert replacement.getsockname()[1]==0
try: replacement.bind(('127.0.0.1',0))
except PermissionError:pass
else:raise AssertionError('replacement acquired bind authority')
with socket.socket() as other:
    try:other.listen(1)
    except PermissionError:pass
    else:raise AssertionError('different FD acquired listen authority')
print('ORIGINAL_ADOPTION_ONLY_NO_NEW_LISTENER')
"""
                result = self.execute(script, inherited=(listener.fileno(),))
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(b"actual-original-response", client.recv(128))
                self.assertEqual("ORIGINAL_ADOPTION_ONLY_NO_NEW_LISTENER", result.stdout.strip())

    def test_filter_refuses_overwrite_or_unrecognized_network_authority(self):
        before = self.filter.read_bytes()
        with self.assertRaises(ValueError):
            boundary.seccomp_filter(self.filter)
        with self.assertRaises(ValueError):
            boundary.seccomp_filter(self.root / "foreign.bpf", network="internet")
        self.assertEqual(before, self.filter.read_bytes())
        self.assertFalse((self.root / "foreign.bpf").exists())
