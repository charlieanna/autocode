"""A completed fixture cannot confuse a reused PID with its recorded reader."""

import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import psutil

from tests import host_reader_cleanup as cleanup


class OwnedReaders(unittest.TestCase):
    def test_unrecorded_child_survives_wrapper_exit_and_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "readers"
            log.write_text("start 11 1.5\n")
            child = mock.Mock(pid=os.getpid() + 1000)
            child.uids.return_value = types.SimpleNamespace(real=getattr(os, "getuid", lambda: None)())
            child.name.return_value = "Python"
            child.status.return_value = psutil.STATUS_RUNNING
            child.cwd.return_value = root
            child.cmdline.return_value = ["python", "/runtime/autocode.py", "--run-dir", root]
            with mock.patch.object(cleanup.psutil, "Process", side_effect=psutil.NoSuchProcess(11)):
                self.assertEqual(cleanup.running_readers(log), [])
            with mock.patch.object(cleanup.psutil, "process_iter", return_value=[child]):
                self.assertEqual(cleanup.unrecorded_readers(root), [child.pid])
                child.cwd.side_effect = psutil.AccessDenied(child.pid)
                with self.assertRaises(psutil.AccessDenied):
                    cleanup.unrecorded_readers(root)

    def test_exited_wrapper_does_not_hide_its_live_cli_child(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "readers"
            log.write_text("start 11 1.5\nchild 12 2.5\n")
            child = mock.Mock(**{"create_time.return_value": 2.5, "status.return_value": psutil.STATUS_RUNNING})
            with mock.patch.object(cleanup.psutil, "Process", side_effect=[psutil.NoSuchProcess(11), child]):
                self.assertEqual(cleanup.running_readers(log), [12])

    def test_live_gone_reused_and_zombie_readers(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "readers"
            log.write_text("start 11 1.5\nheld 11\nstart 12 2.5\nstart 13 3.5\nstart 14 4.5\nend 11 0\n")
            live = mock.Mock(**{"create_time.return_value": 1.5, "status.return_value": psutil.STATUS_RUNNING})
            reused = mock.Mock(**{"create_time.return_value": 99.0, "status.return_value": psutil.STATUS_RUNNING})
            zombie = mock.Mock(**{"create_time.return_value": 4.5, "status.return_value": psutil.STATUS_ZOMBIE})
            with mock.patch.object(
                cleanup.psutil, "Process", side_effect=[live, psutil.NoSuchProcess(12), reused, zombie]
            ):
                self.assertEqual(cleanup.running_readers(log), [11])

    def test_missing_identity_and_access_denied_do_not_claim_exit(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / "readers"
            log.write_text("start 11\n")
            with self.assertRaises(ValueError):
                cleanup.running_readers(log)
            log.write_text("start 11 1.5\n")
            with mock.patch.object(cleanup.psutil, "Process", side_effect=psutil.AccessDenied(11)):
                with self.assertRaises(psutil.AccessDenied):
                    cleanup.running_readers(log)


class PrivateSourceHosts(unittest.TestCase):
    def test_identity_allows_interpreter_flags_but_requires_the_exact_script_and_socket(self):
        with tempfile.TemporaryDirectory() as root:
            uid = 1001
            directory = (Path(root) / "tmp" / f"autocode-{uid}").resolve()
            directory.mkdir(parents=True)
            socket_path = directory / "source-12345678901234567890.sock"
            socket_path.touch()
            tools = Path(cleanup.__file__).resolve().parents[1] / "tools"
            script = tools / "autocode_source_host.py"
            process = mock.Mock(pid=42)
            process.cmdline.return_value = [sys.executable, "-B", str(script), str(socket_path)]
            real_lstat = Path.lstat

            def lstat(path):
                if path == directory:
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=uid)
                if path == socket_path:
                    return types.SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=uid)
                return real_lstat(path)

            with (
                mock.patch.object(cleanup.os, "getuid", return_value=uid, create=True),
                mock.patch.object(Path, "lstat", lstat),
                mock.patch("autocode_source_host.host_status", return_value={"running": True, "pid": 42}) as status,
                mock.patch("autocode_source_host.stop_host", return_value={"stopped": True}) as stop,
                mock.patch.object(cleanup.psutil, "Process", return_value=process),
            ):
                self.assertEqual([42], cleanup.stop_private_source_hosts(root))
                status.assert_called_once_with(socket_path)
                stop.assert_called_once_with(socket_path)
                process.cmdline.return_value = [sys.executable, "-B", str(script), f"{socket_path}.other"]
                with self.assertRaisesRegex(ValueError, "Unexpected fixture source host identity"):
                    cleanup.stop_private_source_hosts(root)
