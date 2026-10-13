"""Native Linux kernel checks using disposable offline workers and IPC barriers."""

import ctypes
import errno
import os
import select
import signal
import socket
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path

import autocode_process as processes
import autocode_session_boundary as boundary

TOOLS = Path(__file__).resolve().parents[1] / "tools"
DEADLINE = 5
BOOTSTRAP = """
import errno, os, socket, sys, threading
sys.path.insert(0, sys.argv[3])
import autocode_session_boundary as boundary
control = socket.socket(fileno=int(sys.argv[1]))
control.settimeout(5)
listener = boundary.install()
boundary.send_listener(control, listener, sys.argv[2])
os.close(listener)
control.send(b'R')
"""


class NativeFixture:
    def __init__(self, worker, control, listener, creator):
        self.worker = worker
        self.control = control
        self.listener = listener
        self.creator = creator
        self.descendants = []

    def receive(self):
        return self.control.recv(4096)

    def request(self):
        readable, _, _ = select.select([self.listener], [], [], DEADLINE)
        if not readable:
            raise AssertionError("Owned fixture did not reach the setsid notification barrier")
        request = boundary.receive_request(self.listener)
        if request is None:
            raise AssertionError("Owned setsid request disappeared at the notification barrier")
        return request

    def own_descendant(self, creator):
        live = processes.process_table({creator["pid"]}).get(creator["pid"])
        if not processes.matches(creator, live):
            raise AssertionError("Fixture descendant lost its birth identity before pidfd capture")
        fd = os.pidfd_open(creator["pid"])
        self.descendants.append((creator, fd))
        return fd

    def collect_descendant(self, creator, fd):
        readable, _, _ = select.select([fd], [], [], DEADLINE)
        if not readable:
            raise AssertionError("Owned descendant did not exit at its release barrier")
        pid, status = os.waitpid(creator["pid"], os.WNOHANG)
        if pid != creator["pid"] or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise AssertionError(f"Owned descendant exit was not collected cleanly: {pid}, {status}")
        self.descendants.remove((creator, fd))
        os.close(fd)


@contextmanager
def native_fixture(body):
    """The test becomes a temporary subreaper for only its own orphan fixture."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0 or libc.prctl(36, 1, 0, 0, 0) != 0:
        raise AssertionError("Cannot establish fixture child-reaping boundary")
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.settimeout(DEADLINE)
    nonce = uuid.uuid4().hex
    worker = None
    fixture = None
    creator = None
    try:
        worker = subprocess.Popen(
            [sys.executable, "-c", BOOTSTRAP + body, str(child.fileno()), nonce, str(TOOLS)],
            pass_fds=(child.fileno(),),
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child.close()
        creator = processes.process_table({worker.pid}).get(worker.pid)
        listener = boundary.receive_listener(parent, nonce)
        fixture = NativeFixture(worker, parent, listener, creator)
        if fixture.receive() != b"R":
            raise AssertionError("Owned bootstrap did not close its listener copy before release")
        yield fixture
    finally:
        parent.close()
        child.close()
        if fixture is not None:
            if fixture.listener is not None:
                os.close(fixture.listener)
            for saved, fd in fixture.descendants:
                row = processes.process_table({saved["pid"]}).get(saved["pid"])
                if processes.matches(saved, row) and not row["state"].startswith("Z"):
                    os.kill(saved["pid"], signal.SIGKILL)
                readable, _, _ = select.select([fd], [], [], DEADLINE)
                if readable:
                    with suppress(ChildProcessError):
                        os.waitpid(saved["pid"], os.WNOHANG)
                os.close(fd)
        if worker is not None:
            if worker.poll() is None:
                row = processes.process_table({worker.pid}).get(worker.pid)
                if creator and processes.matches(creator, row):
                    worker.kill()
            worker.communicate(timeout=DEADLINE)
        libc.prctl(36, previous.value, 0, 0, 0)


@unittest.skipUnless(sys.platform == "linux", "Linux seccomp notification kernel checks")
class NativeSessionBoundaryTests(unittest.TestCase):
    def setUp(self):
        try:
            boundary.require_supported()
        except boundary.UnsupportedBoundary as error:
            self.skipTest(str(error))

    def test_setsid_thread_notification_resolves_group_leader_birth_identity(self):
        body = """
def attempt():
    control.send(str(threading.get_native_id()).encode())
    try:
        os.setsid()
        control.send(b'UnexpectedSuccess')
    except OSError as error:
        control.send(str(error.errno).encode())
thread = threading.Thread(target=attempt)
thread.start()
thread.join()
"""
        with native_fixture(body) as fixture:
            tid = int(fixture.receive())
            request = fixture.request()
            self.assertEqual(tid, request["pid"])
            self.assertNotEqual(fixture.worker.pid, tid)
            creator = boundary.caller_identity(request)
            self.assertEqual(fixture.worker.pid, creator["pid"])
            self.assertTrue(processes.matches(fixture.creator, creator))
            self.assertTrue(boundary.continue_request(fixture.listener, request["id"]))
            self.assertEqual(str(errno.EPERM).encode(), fixture.receive())
            self.assertEqual(0, fixture.worker.wait(timeout=DEADLINE))
            self.assertTrue(boundary.listener_extinct(fixture.listener))

    def test_listener_hangup_waits_for_reparented_inherited_child_exit(self):
        body = """
child = os.fork()
if child:
    os._exit(0)
os.setsid()
control.send(b'Detached')
assert control.recv(1) == b'X'
os._exit(0)
"""
        with native_fixture(body) as fixture:
            request = fixture.request()
            creator = boundary.caller_identity(request)
            self.assertNotEqual(fixture.worker.pid, creator["pid"])
            pidfd = fixture.own_descendant(creator)
            self.assertEqual(0, fixture.worker.wait(timeout=DEADLINE))
            self.assertFalse(boundary.listener_extinct(fixture.listener))
            self.assertTrue(boundary.continue_request(fixture.listener, request["id"]))
            self.assertEqual(b"Detached", fixture.receive())
            detached = processes.process_table({creator["pid"]})[creator["pid"]]
            self.assertEqual(creator["pid"], detached["session"])
            self.assertTrue(processes.matches(creator, detached))
            self.assertFalse(boundary.listener_extinct(fixture.listener))
            fixture.control.send(b"X")
            fixture.collect_descendant(creator, pidfd)
            self.assertTrue(boundary.listener_extinct(fixture.listener))

    def test_replacement_listener_is_denied_after_keeper_listener_closes(self):
        body = """
assert control.recv(1) == b'X'
try:
    replacement = boundary.install()
    os.close(replacement)
    control.send(b'UnexpectedListener')
except boundary.UnsupportedBoundary as error:
    control.send(str(error).encode())
try:
    os.setsid()
    control.send(b'UnexpectedSession')
except OSError as error:
    control.send(str(error.errno).encode())
"""
        with native_fixture(body) as fixture:
            os.close(fixture.listener)
            fixture.listener = None
            fixture.control.send(b"X")
            self.assertIn(b"Operation not permitted", fixture.receive())
            self.assertEqual(str(errno.ENOSYS).encode(), fixture.receive())
            self.assertEqual(0, fixture.worker.wait(timeout=DEADLINE))

    def test_non_dumpable_guardian_blocks_pidfd_descriptor_duplication(self):
        body = """
read_fd, write_fd = os.pipe()
os.write(write_fd, b'x')
control.send(str(read_fd).encode())
assert control.recv(1) == b'H'
boundary.guardian_harden()
control.send(b'Hardened')
assert control.recv(1) == b'X'
"""
        with native_fixture(body) as fixture:
            target_fd = int(fixture.receive())
            pidfd = os.pidfd_open(fixture.worker.pid)
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            try:
                duplicate = libc.syscall(
                    ctypes.c_long(438), ctypes.c_int(pidfd), ctypes.c_int(target_fd), ctypes.c_uint(0)
                )
                if duplicate < 0:
                    self.skipTest(
                        f"Host policy disallows the unhardened pidfd_getfd control: errno {ctypes.get_errno()}"
                    )
                os.close(duplicate)
                fixture.control.send(b"H")
                self.assertEqual(b"Hardened", fixture.receive())
                duplicate = libc.syscall(
                    ctypes.c_long(438), ctypes.c_int(pidfd), ctypes.c_int(target_fd), ctypes.c_uint(0)
                )
                self.assertEqual(-1, duplicate)
                self.assertEqual(errno.EPERM, ctypes.get_errno())
                fixture.control.send(b"X")
                self.assertEqual(0, fixture.worker.wait(timeout=DEADLINE))
            finally:
                os.close(pidfd)

    def test_non_dumpable_guardian_blocks_proc_descriptor_access(self):
        body = """
read_fd, write_fd = os.pipe()
control.send(str(read_fd).encode())
assert control.recv(1) == b'H'
boundary.guardian_harden()
control.send(b'Hardened')
assert control.recv(1) == b'X'
"""
        with native_fixture(body) as fixture:
            target_fd = int(fixture.receive())
            path = f"/proc/{fixture.worker.pid}/fd/{target_fd}"
            duplicate = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            os.close(duplicate)
            fixture.control.send(b"H")
            self.assertEqual(b"Hardened", fixture.receive())
            with self.assertRaises(PermissionError):
                os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fixture.control.send(b"X")
            self.assertEqual(0, fixture.worker.wait(timeout=DEADLINE))


if __name__ == "__main__":
    unittest.main()
