"""Failed capture preparation never leaves custody for an unstarted target."""

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_captured_process as captured


@unittest.skipUnless(sys.platform == "linux", "Linux metadata admission and helper readiness")
class MetadataStartupTests(unittest.TestCase):
    def fresh_command(self, root):
        marker = root / "target-started"
        return [
            sys.executable,
            "-c",
            f"from pathlib import Path;Path({str(marker)!r}).touch();print('fresh metadata')",
        ], marker

    def assert_fresh(self, root, command, marker):
        self.assertFalse(marker.exists(), "preparation failure must not launch the target")
        result = captured.run_metadata(command, timeout=5, cwd=root)
        self.assertEqual((0, "fresh metadata\n", ""), (result.returncode, result.stdout, result.stderr))
        self.assertTrue(marker.is_file())

    def test_missing_target_directory_retains_owned_exec_error_without_blocking_a_fresh_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, marker = self.fresh_command(root)
            with patch.dict(os.environ, {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}):
                with self.assertRaises(FileNotFoundError) as caught:
                    captured.run_metadata(command, timeout=5, cwd=root / "missing-directory")
                self.assertEqual(errno.ENOENT, caught.exception.errno)
                self.assert_fresh(root, command, marker)

    def test_helper_spawn_failure_keeps_its_error_and_creates_no_pending_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, marker = self.fresh_command(root)
            failure = OSError(errno.EAGAIN, "fixture helper spawn refused")
            with patch.dict(os.environ, {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}):
                with patch.object(captured.subprocess, "Popen", side_effect=failure) as launch:
                    with self.assertRaises(OSError) as caught:
                        captured.run_metadata(command, timeout=5, cwd=root)
                    self.assertIs(failure, caught.exception)
                    launch.assert_called_once()
                self.assert_fresh(root, command, marker)

    def test_capture_file_failure_keeps_its_error_and_creates_no_pending_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command, marker = self.fresh_command(root)
            failure = OSError(errno.ENOSPC, "fixture capture allocation refused")
            with patch.dict(os.environ, {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}):
                with patch.object(captured, "_temporary", side_effect=failure):
                    with self.assertRaises(OSError) as caught:
                        captured.run_metadata(command, timeout=5, cwd=root)
                    self.assertIs(failure, caught.exception)
                self.assert_fresh(root, command, marker)


if __name__ == "__main__":
    unittest.main()
