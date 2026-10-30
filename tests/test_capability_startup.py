"""Pre-state startup drops only the two capabilities refused by session ownership."""

import contextlib
import ctypes
import errno
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import autocode_capability_startup as startup

ROOT = Path(__file__).resolve().parents[1]
MASK = (1 << 19) | (1 << 21)


class CapabilityTests(unittest.TestCase):
    def test_reads_bounding_permitted_and_effective_sets(self):
        data = "Name:\tpython\nCapBnd:\t00280001\nCapPrm:\t00200000\nCapEff:\t00080000\n"
        with patch("builtins.open", mock_open(read_data=data)):
            self.assertEqual({"CapBnd": MASK | 1, "CapPrm": 1 << 21, "CapEff": 1 << 19}, startup.capabilities())

    def test_unreadable_missing_duplicate_or_invalid_fields_are_not_assumed_safe(self):
        for data in ("", "CapBnd: xyz\n", "CapBnd: 0\nCapPrm: 0\n", "CapBnd: 0\nCapBnd: 0\nCapPrm: 0\nCapEff: 0\n"):
            with self.subTest(data=data), patch("builtins.open", mock_open(read_data=data)):
                with self.assertRaises(ValueError):
                    startup.capabilities()
        with patch("builtins.open", side_effect=OSError("no proc")), self.assertRaises(OSError):
            startup.capabilities()

    def test_drops_only_requested_bounding_bits(self):
        for mask, expected in ((MASK, [19, 21]), (1 << 19, [19]), (1 << 21, [21]), (1, [])):
            libc = Mock()
            libc.prctl.return_value = 0
            with self.subTest(mask=mask), patch.object(ctypes, "CDLL", return_value=libc):
                startup.drop_bounding(mask)
            self.assertEqual(
                [(24, cap, 0, 0, 0) for cap in expected], [call.args for call in libc.prctl.call_args_list]
            )

    def test_prctl_refusal_is_propagated(self):
        libc = Mock()
        libc.prctl.return_value = -1
        with (
            patch.object(ctypes, "CDLL", return_value=libc),
            patch.object(ctypes, "get_errno", return_value=errno.EPERM),
        ):
            with self.assertRaises(OSError) as error:
                startup.drop_bounding(MASK)
        self.assertEqual(errno.EPERM, error.exception.errno)
        self.assertEqual(1, libc.prctl.call_count)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"KEEP": "unchanged"}, clear=True))
        self.enterContext(patch.object(startup.sys, "platform", "linux"))
        self.caps = self.enterContext(
            patch.object(startup, "capabilities", return_value={"CapBnd": MASK | 1, "CapPrm": MASK, "CapEff": MASK})
        )
        self.drop = self.enterContext(patch.object(startup, "drop_bounding"))
        self.execute = self.enterContext(patch.object(startup.os, "execve"))
        self.enterContext(patch.object(startup, "DROPPED", False))
        self.output = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_reexec_preserves_python_invocation_arguments_and_environment(self):
        for original in (
            ["python", "-B", "tools/autocode.py", "demo", "--directory", "with spaces"],
            ["python", "-m", "autocode_cli", "doctor", "--json"],
        ):
            with self.subTest(original=original), patch.object(sys, "orig_argv", original):
                startup.prepare()
            self.execute.assert_called_with(
                sys.executable, [sys.executable, *original[1:]], {"KEEP": "unchanged", startup.REEXEC: "1"}
            )
        self.assertEqual([((MASK,), {}), ((MASK,), {})], self.drop.call_args_list)
        self.assertFalse(startup.DROPPED)
        self.assertEqual("", self.output.getvalue())

    def test_non_linux_and_explicit_opt_out_do_not_inspect_or_mutate(self):
        with patch.object(startup.sys, "platform", "darwin"):
            startup.prepare()
        with patch.dict(os.environ, {startup.OPT_OUT: "1"}):
            startup.prepare()
        self.caps.assert_not_called()
        self.drop.assert_not_called()
        self.execute.assert_not_called()

    def test_unprivileged_startup_does_not_reexec(self):
        self.caps.return_value = {"CapBnd": 1, "CapPrm": 1, "CapEff": 1}
        startup.prepare()
        self.drop.assert_not_called()
        self.execute.assert_not_called()
        self.assertFalse(startup.DROPPED)

    def test_failures_leave_existing_admission_in_charge(self):
        for operation, error in (
            (self.caps, OSError("no proc")),
            (self.caps, ValueError("invalid caps")),
            (self.drop, OSError("seccomp refusal")),
            (self.drop, AttributeError("no prctl")),
            (self.execute, OSError("exec refused")),
        ):
            with self.subTest(error=error):
                operation.side_effect = error
                startup.prepare()
                self.assertFalse(startup.DROPPED)
                operation.side_effect = None
        self.assertEqual("", self.output.getvalue())

    def test_reexec_receipt_requires_observed_absence_and_never_retries(self):
        for field in ("CapBnd", "CapPrm", "CapEff"):
            with patch.dict(os.environ, {startup.REEXEC: "1"}):
                self.caps.return_value = {"CapBnd": 0, "CapPrm": 0, "CapEff": 0, field: MASK}
                startup.prepare()
                self.assertFalse(startup.DROPPED)
        with patch.dict(os.environ, {startup.REEXEC: "1"}):
            self.caps.return_value = {"CapBnd": 1, "CapPrm": 1, "CapEff": 1}
            startup.prepare()
            self.assertNotIn(startup.REEXEC, os.environ, "child CLIs must not inherit a stale receipt")
        self.assertTrue(startup.DROPPED)
        self.assertEqual(
            "autocode: dropped CAP_SYS_PTRACE and CAP_SYS_ADMIN for this invocation\n", self.output.getvalue()
        )
        self.drop.assert_not_called()
        self.execute.assert_not_called()

    def test_cli_prepares_before_entering_the_run(self):
        import autocode

        calls = []

        def stop_before_run():
            calls.append("clock")
            raise RuntimeError("stop before run")

        with (
            patch.object(startup, "prepare", side_effect=lambda: calls.append("prepare")),
            patch.object(autocode, "now", side_effect=stop_before_run),
            patch.object(autocode.processes, "interrupts_held", return_value=contextlib.nullcontext()),
            patch.object(sys, "stdout", io.StringIO()),
            patch.object(sys, "stderr", io.StringIO()),
        ):
            self.assertEqual(2, autocode.cli())
        self.assertEqual(["prepare", "clock"], calls)

    def test_doctor_records_drop_only_after_session_admission(self):
        import autocode_doctor as doctor

        with patch.object(startup, "DROPPED", True):
            check = doctor.session_check(lambda: None)
            self.assertEqual(doctor.OK, check.status)
            self.assertIn("automatically dropped", check.detail)
            self.assertIn("CAP_SYS_PTRACE", check.detail)
            with patch.object(
                doctor.session_boundary,
                "require_supported",
                side_effect=doctor.session_boundary.UnsupportedBoundary("not admitted"),
            ) as require:
                check = doctor.session_check(require)
            self.assertEqual(doctor.MISSING, check.status)
            self.assertNotIn("automatically dropped", check.detail)


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_TEST_PRIVILEGED_STARTUP") == "1",
    "requires an explicitly privileged Linux test subprocess",
)
class LinuxCapabilityStartupTests(unittest.TestCase):
    def test_real_reexec_drops_exactly_two_caps_and_preserves_pid(self):
        before = startup.capabilities()
        self.assertEqual(MASK, before["CapBnd"] & MASK, "qualification needs both capabilities initially present")
        code = """
import json, os
import autocode_capability_startup as startup
os.environ.setdefault('ORIGINAL_PID', str(os.getpid()))
startup.prepare()
import autocode_session_boundary as boundary
boundary.require_supported()
print(json.dumps({'caps': startup.capabilities(), 'same_pid': str(os.getpid()) == os.environ['ORIGINAL_PID'], 'dropped': startup.DROPPED}))
"""
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "tools"))
        environment.pop(startup.REEXEC, None)
        environment.pop(startup.OPT_OUT, None)
        result = subprocess.run(
            [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(0, result.returncode, result.stderr)
        observed = json.loads(result.stdout)
        self.assertTrue(observed["same_pid"])
        self.assertTrue(observed["dropped"])
        self.assertEqual({key: value & ~MASK for key, value in before.items()}, observed["caps"])
        self.assertEqual(1, result.stderr.count("autocode: dropped"))

    def test_demo_opt_out_keeps_the_existing_cleanup_pause(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, AUTOCODE_NO_CAPABILITY_DROP="1")
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/autocode.py"), "demo", "--directory", str(Path(directory) / "demo")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("PAUSED_PROCESS_CLEANUP", result.stdout + result.stderr)
        self.assertIn("CAP_SYS", result.stdout + result.stderr)
        self.assertNotIn("autocode: dropped", result.stderr)

    def test_doctor_json_reports_observed_drop_without_polluting_stdout(self):
        environment = dict(os.environ)
        environment.pop(startup.REEXEC, None)
        environment.pop(startup.OPT_OUT, None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/autocode.py"), "doctor", "--engine", "codex", "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        check = next(row for row in json.loads(result.stdout)["checks"] if row["name"] == "session")
        self.assertEqual("ok", check["status"])
        self.assertIn("automatically dropped CAP_SYS_PTRACE and CAP_SYS_ADMIN", check["detail"])
        self.assertEqual(1, result.stderr.count("autocode: dropped"))

    def test_failed_drop_or_reexec_preserves_existing_refusal(self):
        for target in ("drop_bounding", "os.execve"):
            code = f"""
from unittest.mock import patch
import autocode_capability_startup as s
with patch('autocode_capability_startup.{target}', side_effect=OSError('operation refused')):
    s.prepare()
import autocode_session_boundary as b
b.require_supported()
"""
            environment = dict(os.environ, PYTHONPATH=str(ROOT / "tools"))
            environment.pop(startup.REEXEC, None)
            environment.pop(startup.OPT_OUT, None)
            with self.subTest(target=target):
                result = subprocess.run(
                    [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=30
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("refuses CAP_SYS_PTRACE or CAP_SYS_ADMIN", result.stderr)
                self.assertNotIn("autocode: dropped", result.stderr)

    def test_opt_out_preserves_existing_refusal(self):
        code = "import autocode_capability_startup as s; s.prepare(); import autocode_session_boundary as b; b.require_supported()"
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "tools"), AUTOCODE_NO_CAPABILITY_DROP="1")
        result = subprocess.run(
            [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=30
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("refuses CAP_SYS_PTRACE or CAP_SYS_ADMIN", result.stderr)
        self.assertNotIn("autocode: dropped", result.stderr)
