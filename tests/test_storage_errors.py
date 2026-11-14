"""Storage and verification diagnostics through the public CLI, without real faults."""

import errno
import subprocess
import tempfile
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

    def test_opted_in_captured_git_diagnostic_supports_text_bytes_and_invalid_utf8(self):
        for stderr, expected in (
            ("fatal: cannot prepare source\n", "fatal: cannot prepare source"),
            (b"fatal: cannot prepare source\n", "fatal: cannot prepare source"),
            (b"fatal: cannot read \xff source\n", "fatal: cannot read \ufffd source"),
        ):
            with self.subTest(stderr=stderr):
                error = subprocess.CalledProcessError(128, ["/usr/bin/git", "fast-import"], stderr=stderr)
                message = storage.describe(
                    error, operation="preparing work", location="/tmp/run", include_captured_stderr=True
                )
                self.assertIn(str(error), message)
                self.assertIn(expected, message)
                self.assertNotIn("Disk full", message)

    def test_opted_in_stderr_is_redacted_before_the_explicit_output_bound(self):
        secret = "opaque_credential_near_the_truncation_boundary"
        stderr = "x" * 1989 + "\ntoken=" + secret + "\n" + "y" * 5000
        error = subprocess.CalledProcessError(128, ["/usr/bin/git", "fast-import"], stderr=stderr)
        message = storage.describe(error, operation="preparing work", location="/tmp/run", include_captured_stderr=True)
        self.assertIn("[REDACTED]", message)
        self.assertNotIn(secret[:10], message)
        self.assertNotIn("y", message)
        self.assertTrue(message.endswith("[stderr truncated]"))
        self.assertLessEqual(len(message), len(str(error)) + 2000 + 80)

    def test_opted_in_empty_stderr_keeps_the_exception_and_never_uses_stdout(self):
        for stderr in (None, b"", "", " \n"):
            with self.subTest(stderr=stderr):
                error = subprocess.CalledProcessError(128, ["git", "reset"], output="untrusted output", stderr=stderr)
                self.assertEqual(
                    str(error),
                    storage.describe(
                        error, operation="preparing work", location="/tmp/run", include_captured_stderr=True
                    ),
                )

    def test_opted_in_disk_full_explains_storage_without_resume_advice(self):
        error = subprocess.CalledProcessError(
            128,
            ["git", "fast-import"],
            stderr=b"fatal: cannot write object: No space left on device\npassword=opaque_secret_value\n",
        )
        message = storage.describe(
            error, operation="preparing a verification copy", location="/tmp/run", include_captured_stderr=True
        )
        self.assertIn("Disk full while preparing a verification copy", message)
        self.assertIn("/tmp/run", message)
        self.assertNotIn("resume", message.lower())
        self.assertNotIn("Free space", message)
        self.assertIn("No space left on device", message)
        self.assertIn("[REDACTED]", message)
        self.assertNotIn("opaque_secret_value", message)

    def test_opted_in_non_subprocess_prose_does_not_gain_a_captured_diagnostic(self):
        error = RuntimeError("invalid source")
        self.assertEqual(
            str(error),
            storage.describe(error, operation="preparing work", location="/tmp/run", include_captured_stderr=True),
        )


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
        self.assertEqual([], flow.trace("builder"))
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
        self.assertNotIn("resume this run", view["view"]["stop_reason"])
        self.assertEqual(flow.approved_token, view["contract_token"])
        self.assertEqual("print('hello')\n", (flow.project / "main.py").read_text())
        self.assertEqual([], flow.trace("tester"))
        built = source.snapshot(flow.project)
        flow.drive("--resume-paused")
        final = flow.status()
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertEqual(flow.approved_token, final["contract_token"])
        self.assertEqual(built, source.snapshot(flow.project))
        self.assertEqual(1, len(flow.trace("builder")))
        self.assertTrue(flow.trace("tester"))

    def test_verification_copy_git_failure_keeps_diagnostic_before_any_tester_launch(self):
        flow = self.flow
        flow.seed()
        controls_before = set(flow.run.glob("tool-containment-*"))
        source_at_failure = []
        real_run = subprocess.run

        def fail_import(command, *args, **kwargs):
            if command[:1] == ["/usr/bin/git"] and "fast-import" in command:
                source_at_failure.append(source.snapshot(flow.project))
                # Exercise an actual collected Git failure after the copy took
                # its source snapshot, without changing the original product.
                with tempfile.TemporaryFile() as stream:
                    stream.write(b"verification-copy-failure token=private_test_credential\n")
                    stream.seek(0)
                    return real_run(command, *args, **{**kwargs, "stdin": stream})
            return real_run(command, *args, **kwargs)

        with patch.object(copies.subprocess, "run", side_effect=fail_import):
            flow.drive(codes=(2,))
        view = flow.status()
        self.assertIn("unsupported command: verification-copy-failure", view["view"]["stop_reason"].lower())
        self.assertIn("[REDACTED]", view["view"]["stop_reason"])
        self.assertNotIn("private_test_credential", str(view))
        self.assertFalse(view["view"]["done"])
        self.assertIsNone(view["view"]["needs"])
        self.assertIsNone(view["view"]["next_command"])
        self.assertEqual("stopped", view["view"]["recovery"]["category"])
        self.assertEqual("stopped", view["view"]["summary"]["state"])
        self.assertEqual(controls_before, set(flow.run.glob("tool-containment-*")))
        self.assertEqual(flow.approved_token, view["contract_token"])
        self.assertEqual("print('hello')\n", (flow.project / "main.py").read_text())
        self.assertEqual([source.snapshot(flow.project)], source_at_failure)
        self.assertEqual([], flow.trace("tester"))
        self.assertEqual(1, len(flow.trace("builder")))

    def test_joint_planning_settings_cannot_release_unknown_preparation(self):
        flow = self.flow
        flow.seed()
        self.assertFalse(flow.status()["settings"].get("joint_planning"))
        failure = subprocess.CalledProcessError(128, ["/usr/bin/git", "fast-import"], stderr=b"fatal: source unknown")
        # This deliberate pre-context failure has no authenticated source
        # observation, so a quiet terminal outcome would be unjustified.
        with patch.object(copies, "create", side_effect=failure):
            flow.drive(codes=(2,))
        held = flow.status()
        self.assertEqual("blocked", held["view"]["recovery"]["category"])
        before = source.snapshot(flow.project)
        calls = flow.trace()
        approvals = held["view"]["approvals"]

        flow.drive("--resume-paused", "--joint-planning", codes=(2,))
        after = flow.status()
        self.assertTrue(after["settings"]["joint_planning"])
        self.assertEqual("PAUSED_VERIFICATION_PREPARATION", after["status"])
        self.assertEqual("blocked", after["view"]["recovery"]["category"])
        self.assertEqual("blocked", after["view"]["summary"]["state"])
        self.assertFalse(after["view"]["done"])
        self.assertIsNone(after["view"]["needs"])
        self.assertIsNone(after["view"]["next_command"])
        self.assertEqual(approvals, after["view"]["approvals"])
        self.assertEqual(before, source.snapshot(flow.project))
        self.assertEqual(calls, flow.trace())
