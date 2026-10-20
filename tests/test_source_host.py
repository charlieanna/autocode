"""The source host answers exactly what a local snapshot would (autocode_source_host, #1707)."""

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_source_host as host
import autocode_util as util

REAL_SOCKET_PATH = host.socket_path


def git(root, *args):
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=F", "-c", "user.email=f@example.test", *args],
        check=True,
        capture_output=True,
    )


class SourceHostTests(unittest.TestCase):
    def setUp(self):
        # A Unix socket path must stay short; the default temporary folder on macOS is not.
        self.temp = Path(tempfile.mkdtemp(dir="/tmp" if os.path.isdir("/tmp") else None))
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.root = self.temp / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        (self.root / "app.py").write_text("VALUE = 1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "seed")
        self.socket = self.temp / "h.sock"
        patcher = mock.patch.object(host, "socket_path", return_value=self.socket)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(host._state.update, {"failed": False, "started": False})
        self.addCleanup(util.use_snapshot_service, None)

    def serve(self):
        threading.Thread(target=host.serve, args=(self.socket,), daemon=True).start()
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            self.assertLess(time.monotonic(), deadline, "the host did not start")
            time.sleep(0.02)

    def local(self, root=None):
        util.use_snapshot_service(None)
        return util.snapshot(root or self.root)

    def test_the_host_answers_exactly_what_a_local_snapshot_does_including_after_an_edit(self):
        self.serve()
        self.assertEqual(self.local(), host.snapshot(self.root, False))
        (self.root / "app.py").write_text("VALUE = 2\n")
        (self.root / "new.py").write_text("NEW = True\n")
        edited = host.snapshot(self.root, False)
        self.assertEqual(self.local(), edited)
        self.assertIn("new.py", edited["files"])

    def test_a_snapshot_the_host_cannot_take_stops_the_caller_the_same_way(self):
        self.serve()
        outside = self.temp / "not-a-repository"
        outside.mkdir()
        with self.assertRaises(util.Paused) as local:
            self.local(outside)
        with self.assertRaises(util.Paused) as hosted:
            host.snapshot(outside, False)
        self.assertEqual(
            (local.exception.status, str(local.exception)), (hosted.exception.status, str(hosted.exception))
        )

    def test_without_a_host_the_caller_starts_one_once_and_takes_its_own_snapshot(self):
        with mock.patch.object(host, "subprocess") as spawn:  # this module's reference only
            self.assertIsNone(host.snapshot(self.root, False))
            self.assertIsNone(host.snapshot(self.root, False))
            self.assertEqual(1, spawn.Popen.call_count)
            util.use_snapshot_service(host.snapshot)
            served = util.snapshot(self.root)
        self.assertEqual(self.local(), served)

    def test_only_the_setting_routes_snapshots_through_the_host(self):
        with mock.patch.dict(os.environ, {host.SETTING: ""}):
            host.install()
        self.assertIsNone(util._SNAPSHOT_SERVICE)
        with mock.patch.dict(os.environ, {host.SETTING: "1"}):
            host.install()
        self.assertIs(host.snapshot, util._SNAPSHOT_SERVICE)

    def test_a_host_for_another_git_environment_is_never_asked(self):
        base = {"HOME": "/home/a", "PATH": "/bin", "TERM": "xterm"}
        same = REAL_SOCKET_PATH(base)
        self.assertEqual(same, REAL_SOCKET_PATH({**base, "TERM": "dumb"}), "unrelated settings share a host")
        for changed in ({"GIT_DIR": "/elsewhere"}, {"HOME": "/home/b"}, {"PATH": "/opt/bin"}):
            with self.subTest(changed=changed):
                self.assertNotEqual(same, REAL_SOCKET_PATH({**base, **changed}))
        self.assertEqual(0o700, same.parent.stat().st_mode & 0o777 if same.parent.exists() else 0o700)


if __name__ == "__main__":
    unittest.main()
