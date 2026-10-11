"""Disk-full stops and recovery through the public CLI, without filling the disk."""

import errno
import subprocess
import unittest
from unittest.mock import patch

import autocode_dispatch as dispatch
import autocode_source_snapshot as source
import autocode_storage_errors as storage
import autocode_verification_copy as copies

from tests import test_failure_routing_cli as routing_fixture


class StorageErrorTests(unittest.TestCase):
    def test_other_failures_and_quoted_filenames_keep_their_original_diagnostic(self):
        errors = [
            ValueError("invalid source"),
            OSError(errno.EACCES, "Permission denied", "/tmp/project"),
            RuntimeError("fatal: cannot prepare: No space left on device"),
            "fatal: cannot open 'No space left on device': No such file or directory",
            "fatal: cannot open 'No space left on device'",
        ]
        for error in errors:
            with self.subTest(error=str(error)):
                self.assertEqual(
                    str(error),
                    storage.describe(
                        error, operation="preparing work", location="/tmp/run", git_stderr=isinstance(error, str)
                    ),
                )

    def test_wrapped_os_error_names_the_actual_destination_and_retains_the_cause(self):
        cause = OSError(errno.ENOSPC, "No space left on device", "/other-volume/run/source.py")
        error = RuntimeError("verification copy failed")
        error.__cause__ = cause
        message = storage.describe(error, operation="preparing work", location="/tmp/run")
        self.assertIn("Disk full", message)
        self.assertIn("/other-volume/run/source.py", message)
        self.assertIn("Free space", message)
        self.assertIn(str(error), message)
        self.assertIs(cause, error.__cause__)

    def test_captured_git_subprocess_stderr_survives_opaque_exception_text(self):
        error = subprocess.CalledProcessError(
            128, ["git", "reset", "--hard"], stderr=b"fatal: cannot write object: No space left on device\n"
        )
        message = storage.describe(error, operation="preparing work", location="/tmp/run")
        self.assertIn("Disk full", message)
        self.assertIn("/tmp/run", message)
        self.assertIn("fatal: cannot write object: No space left on device", message)
        self.assertIn(str(error), message)

    def test_non_disk_full_git_subprocess_error_keeps_the_original_exception_text(self):
        error = subprocess.CalledProcessError(128, ["git", "reset"], stderr=b"fatal: invalid revision\n")
        self.assertEqual(str(error), storage.describe(error, operation="preparing work", location="/tmp/run"))


class DiskFullCLI(unittest.TestCase):
    def setUp(self):
        self.flow = routing_fixture.FailureRoutingCLI(methodName="runTest")
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def test_parallel_git_disk_full_explains_the_volume_before_launch_and_recovers(self):
        flow = self.flow
        flow.seed_parallel()
        before = source.snapshot(flow.project)
        real_run = subprocess.run

        def no_space(command, *args, **kwargs):
            if command[:2] == ["git", "-C"] and "worktree" in command and "add" in command:
                return subprocess.CompletedProcess(
                    command, 128, b"", b"fatal: could not create work tree: No space left on device\n"
                )
            return real_run(command, *args, **kwargs)

        with patch.object(dispatch.subprocess, "run", side_effect=no_space):
            flow.drive(codes=(2,))
        view = flow.status()
        self.assertEqual("PAUSED_ORCHESTRATOR_GIT", view["status"])
        self.assertIn("Disk full", view["view"]["stop_reason"])
        self.assertIn(str(flow.project), view["view"]["stop_reason"])
        self.assertIn("Free space", view["view"]["stop_reason"])
        self.assertIn("No space left on device", view["view"]["stop_reason"])
        self.assertEqual(flow.approved_token, view["contract_token"])
        self.assertEqual(before, source.snapshot(flow.project))
        self.assertEqual([], flow.trace("terra"))
        flow.drive("--resume-paused")
        final = flow.status()
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertEqual(flow.approved_token, final["contract_token"])
        self.assertEqual("print('hello')\n", (flow.project / "main.py").read_text())
        self.assertEqual("preserved sibling\n", (flow.project / "sibling.txt").read_text())

    def test_verification_copy_disk_full_explains_the_volume_and_preserves_accepted_work(self):
        flow = self.flow
        flow.seed()
        controls_before = set(flow.run.glob("tool-containment-*"))
        with patch.object(copies, "create", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            flow.drive(codes=(2,))
        view = flow.status()
        self.assertEqual("PAUSED_STALE_VALIDATION", view["status"])
        self.assertEqual(controls_before, set(flow.run.glob("tool-containment-*")))
        self.assertIn("Disk full", view["view"]["stop_reason"])
        self.assertIn(str(flow.run), view["view"]["stop_reason"])
        self.assertIn("Free space", view["view"]["stop_reason"])
        self.assertEqual(flow.approved_token, view["contract_token"])
        self.assertEqual("print('hello')\n", (flow.project / "main.py").read_text())
        self.assertEqual([], flow.trace("sol"))
        built = source.snapshot(flow.project)
        flow.drive("--resume-paused")
        final = flow.status()
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertEqual(flow.approved_token, final["contract_token"])
        self.assertEqual(built, source.snapshot(flow.project))
        self.assertEqual(1, len(flow.trace("terra")))
        self.assertTrue(flow.trace("sol"))
