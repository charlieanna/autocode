"""autocode_util on native Windows (#698): the portable file lock (fcntl on POSIX, msvcrt on
Windows) and atomic JSON writes, which cannot sync a directory there.

Both lock backends are selected through ``autocode_util._WINDOWS`` and a fake module in
``sys.modules``, so every case runs deterministically on any host, including the
Windows CI job.
"""

import errno
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_util as util


class FakeMsvcrt(types.ModuleType):
    """msvcrt.locking over (fd -> owner) byte locks, shared across descriptors of one path."""

    LK_UNLCK, LK_LOCK, LK_NBLCK, LK_RLCK, LK_NBRLCK = 0, 1, 2, 3, 4

    def __init__(self, busy_errno=errno.EACCES):
        super().__init__("msvcrt")
        self.busy_errno = busy_errno
        self.held = {}  # (inode, offset) -> fd
        self.calls = []
        self.fail_next = 0

    def locking(self, fd, mode, nbytes):
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        self.calls.append((mode, offset, nbytes))
        key = (os.fstat(fd).st_ino, offset)
        if mode == self.LK_UNLCK:
            if self.held.get(key) != fd:
                raise OSError(errno.EACCES, "Permission denied")
            del self.held[key]
            return
        if mode != self.LK_NBLCK:
            raise AssertionError(f"lock_file must not use msvcrt mode {mode}")
        if self.fail_next:
            self.fail_next -= 1
            raise OSError(self.busy_errno, "Resource deadlock avoided")
        if key in self.held:
            raise OSError(self.busy_errno, "Resource deadlock avoided")
        self.held[key] = fd


class WindowsBackendTests(unittest.TestCase):
    def setUp(self):
        self.msvcrt = FakeMsvcrt()
        for patcher in (
            patch.object(util, "_WINDOWS", True),
            patch.dict(sys.modules, {"msvcrt": self.msvcrt}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def open(self, name="writer.lock"):
        handle = (self.root / name).open("a+")
        self.addCleanup(handle.close)
        return handle

    def test_second_handle_is_refused_without_waiting(self):
        first, second = self.open(), self.open()
        util.lock_file(first)
        with patch.object(util, "sleep") as sleep:
            with self.assertRaises(BlockingIOError):
                util.lock_file(second)
        sleep.assert_not_called()
        util.unlock_file(first)
        util.lock_file(second)  # free again once released

    def test_locks_one_byte_past_content_and_keeps_file_position(self):
        handle = self.open()
        handle.write("pid 42\n")
        handle.flush()
        before = os.lseek(handle.fileno(), 0, os.SEEK_CUR)
        util.lock_file(handle)
        util.unlock_file(handle)
        self.assertEqual(
            self.msvcrt.calls,
            [
                (FakeMsvcrt.LK_NBLCK, util._WINDOWS_LOCK_OFFSET, 1),
                (FakeMsvcrt.LK_UNLCK, util._WINDOWS_LOCK_OFFSET, 1),
            ],
        )
        self.assertEqual(os.lseek(handle.fileno(), 0, os.SEEK_CUR), before)
        self.assertGreater(util._WINDOWS_LOCK_OFFSET, len("pid 42\n"))

    def test_deadlock_errno_also_means_busy(self):
        self.msvcrt.busy_errno = errno.EDEADLOCK if hasattr(errno, "EDEADLOCK") else errno.EDEADLK
        first, second = self.open(), self.open()
        util.lock_file(first)
        with self.assertRaises(BlockingIOError):
            util.lock_file(second)

    def test_blocking_lock_retries_through_the_shared_wait(self):
        handle = self.open()
        self.msvcrt.fail_next = 2
        with patch.object(util, "sleep") as sleep:
            util.lock_file(handle, blocking=True)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(util.WINDOWS_LOCK_RETRY_SECONDS)
        self.assertEqual([call[0] for call in self.msvcrt.calls], [FakeMsvcrt.LK_NBLCK] * 3)

    def test_other_errors_are_not_mistaken_for_a_busy_lock(self):
        handle = self.open()
        self.msvcrt.fail_next = 1
        self.msvcrt.busy_errno = errno.EBADF
        with self.assertRaises(OSError) as raised:
            util.lock_file(handle)
        self.assertNotIsInstance(raised.exception, BlockingIOError)
        self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_accepts_a_file_descriptor(self):
        handle = self.open()
        util.lock_file(handle.fileno())
        util.unlock_file(handle.fileno())
        self.assertEqual(self.msvcrt.held, {})

    def test_workspace_and_run_locks_pause_when_busy(self):
        with util.workspace_lock(self.root):
            with self.assertRaises(util.Paused) as raised:
                with util.workspace_lock(self.root):
                    pass
        self.assertEqual(raised.exception.status, "PAUSED_WORKSPACE_BUSY")
        with util.workspace_lock(self.root):  # released on exit
            pass
        run = self.root / "run"
        with util.run_lock(run):
            with self.assertRaises(util.Paused) as raised:
                with util.run_lock(run):
                    pass
        self.assertEqual(raised.exception.status, "PAUSED_RUN_BUSY")
        self.assertEqual(self.msvcrt.held, {})


class PosixBackendTests(unittest.TestCase):
    def setUp(self):
        self.fcntl = types.ModuleType("fcntl")
        self.fcntl.LOCK_EX, self.fcntl.LOCK_NB, self.fcntl.LOCK_UN = 2, 4, 8
        self.calls = []
        self.fcntl.flock = lambda handle, operation: self.calls.append((handle, operation))
        for patcher in (
            patch.object(util, "_WINDOWS", False),
            patch.dict(sys.modules, {"fcntl": self.fcntl}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_uses_flock_non_blocking_by_default(self):
        util.lock_file("handle")
        util.lock_file("handle", blocking=True)
        util.unlock_file("handle")
        self.assertEqual(self.calls, [("handle", 2 | 4), ("handle", 2), ("handle", 8)])


class AtomicJsonTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state" / "run.json"
        self.opened = []
        real_open = os.open

        def spy(path, flags, *args, **kwargs):
            if Path(path).is_dir():
                if util._WINDOWS:
                    raise PermissionError(errno.EACCES, "Permission denied", str(path))
                self.opened.append(Path(path))
            return real_open(path, flags, *args, **kwargs)

        patcher = patch.object(util.os, "open", side_effect=spy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_windows_writes_without_opening_the_directory(self):
        with patch.object(util, "_WINDOWS", True):
            util.atomic_json(self.path, {"status": "RUNNING"})
            util.atomic_json(self.path, {"status": "PAUSED"})
        self.assertEqual({"status": "PAUSED"}, json.loads(self.path.read_text()))
        self.assertEqual(["run.json"], sorted(p.name for p in self.path.parent.iterdir()))

    def test_posix_still_syncs_the_directory_after_the_rename(self):
        with patch.object(util, "_WINDOWS", False):
            util.atomic_json(self.path, {"status": "RUNNING"})
        self.assertEqual([self.path.parent], self.opened)
        self.assertEqual({"status": "RUNNING"}, json.loads(self.path.read_text()))


if __name__ == "__main__":
    unittest.main()
