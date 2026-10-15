"""Original supervisor cancellation survives failed held-root admission.

Controlled clocks and event handshakes exercise the real wait/caller functions;
no provider, subprocess, real-time sleep or image authority is involved.
"""

import contextlib
import signal
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_process as processes
from tests import test_autocode as caller_fixture


class ControlledStage:
    def __init__(self, case, *, kind="idle", cleanup_error=None, early_observer=False):
        self.case, self.kind = case, kind
        self.clock = [0.0]
        self.captured = False
        self.observed = threading.Event()
        self.second_poll = threading.Event()
        self.signalled = threading.Event()
        self.trace, self.timers, self.rows = [], [], []
        self.cleanup_error = cleanup_error
        self.early_observer = early_observer
        self.child = self.Child(self)
        self.monitor = self.Monitor(self)
        self.original = processes.ProcessError("original held-root authentication refused")

    class Child:
        pid = 987654321

        def __init__(self, owner):
            self.owner, self.returncode = owner, None

        def poll(self):
            self.owner.case.assertTrue(self.owner.captured, "ordinary poll before root capture (#621)")
            self.owner.trace.append("poll")
            return self.returncode

        def wait(self, timeout=None):
            self.owner.case.assertTrue(self.owner.signalled.wait(3), "original stop handshake missing")
            return self.returncode

        def kill(self):
            self.returncode = -signal.SIGKILL
            self.owner.signalled.set()

    class Monitor:
        idle_limit, tool_limit = 300, 600

        def __init__(self, owner):
            self.owner, self.calls = owner, 0

        def poll(self, **_):
            self.calls += 1
            self.owner.trace.append("observe")
            self.owner.observed.set()
            if self.calls > 1:
                self.owner.second_poll.set()
            return {
                processes.POLL_SAMPLED_MONOTONIC: self.owner.clock[0],
                "idle_seconds": self.owner.clock[0],
                "tool_elapsed_seconds": self.owner.clock[0] if self.owner.kind == "tool" else None,
                "idle_limit_seconds": self.idle_limit,
                "tool_limit_seconds": self.tool_limit,
            }

    def tree(self, pid, checkpoint):
        owner = self

        class Tree:
            def __init__(self):
                self.pid, self.known = pid, {}

            def capture_root(self):
                if owner.early_observer:
                    owner.case.assertTrue(owner.observed.wait(3), "observer must run during root capture (#621)")
                owner.captured = True
                owner.trace.append("capture")
                self.known[pid] = {"pid": pid, "birth_identity": 1.0}
                return self.known[pid]

            def sample(self, **_):
                return list(self.known.values())

            def stop(self, child):
                owner.trace.append("cleanup")
                if owner.cleanup_error is not None:
                    raise owner.cleanup_error
                if child.returncode is None:
                    child.returncode = -signal.SIGTERM
                    owner.signalled.set()
                child.wait(timeout=3)

        return Tree()

    def timer(self, seconds, function, args=()):
        owner = self

        class Timer:
            daemon = False

            def start(self):
                owner.timers.append((seconds, function, args))

            def cancel(self):
                pass

        return Timer()

    def signal(self, pid, sig):
        self.case.assertEqual(self.child.pid, pid)
        self.case.assertTrue(self.captured)
        self.trace.append("signal")
        self.child.returncode = -sig
        self.signalled.set()

    @contextlib.contextmanager
    def bound(self):
        with (
            patch.object(processes, "ProcessTree", self.tree),
            patch.object(processes.util, "monotonic", side_effect=lambda: self.clock[0]),
            patch.object(processes.threading, "Timer", self.timer),
            patch.object(processes.os, "getpgid", return_value=self.child.pid),
            patch.object(processes.os, "killpg", side_effect=self.signal),
        ):
            yield self

    def wait(self, *, original=None, release=False, cancel=True, normal=False):
        # The original process supervisor owns the decision; the callback merely
        # advances its controlled clock and waits for its actual signal event.
        error = original or self.original
        with tempfile.TemporaryDirectory() as directory:
            read_fd, write_fd = processes.os.pipe()
            processes.os.set_blocking(write_fd, False)
            try:
                def on_root(_tree, token):
                    self.case.assertTrue(self.observed.wait(3))
                    if self.kind == "tool":
                        # Second poll proves the first full tool snapshot was
                        # published before advancing the supervised clock.
                        self.case.assertTrue(self.second_poll.wait(3))
                    if cancel:
                        if self.kind == "stage":
                            _seconds, stop, args = next(row for row in self.timers if row[0] == 900)
                            self.clock[0] = 901.0
                            stop(*args)
                        else:
                            self.clock[0] = 601.0 if self.kind == "tool" else 301.0
                        self.case.assertTrue(self.signalled.wait(3), "original watchdog did not signal")
                    if release:
                        token(write_fd, b"target-release")
                        self.case.fail("cancelled held target was released")
                    if normal:
                        if not cancel:
                            self.child.returncode = 0
                            self.signalled.set()
                        return
                    raise error

                try:
                    return processes.wait_for_stage(
                        self.child, 900, lambda rows: self.rows.extend(rows),
                        activity=self.monitor, on_root=on_root,
                    )
                finally:
                    processes.os.close(write_fd)
                    write_fd = None
                    self.case.assertEqual(b"", processes.os.read(read_fd, 512), "target token must remain absent")
            finally:
                if write_fd is not None:
                    processes.os.close(write_fd)
                processes.os.close(read_fd)


class ExceptionalTimeoutTests(unittest.TestCase):
    def normal_inside_handled_error(self, *, cancel):
        ambient = processes.ProcessError("caller already handled this unrelated error")
        original_fact = {"kind": "caller-owned", "reason": "preserve exact prior attributes"}
        ambient.stage_timeout = original_fact
        ambient.caller_data = ["must remain unchanged"]
        before = dict(ambient.__dict__)
        stage = ControlledStage(self)
        try:
            raise ambient
        except processes.ProcessError as caught:
            with stage.bound():
                result = stage.wait(cancel=cancel, normal=True)
            self.assertIs(ambient, caught)
            self.assertEqual(before, caught.__dict__)
            self.assertIs(original_fact, caught.stage_timeout)
            self.assertEqual((-15, True) if cancel else (0, False), result)
        self.assertTrue(stage.rows)

    def test_normal_no_timeout_return_preserves_ambient_handled_error(self):
        self.normal_inside_handled_error(cancel=False)

    def test_normal_original_timeout_return_preserves_ambient_handled_error(self):
        self.normal_inside_handled_error(cancel=True)

    def assert_timeout(self, stage, error):
        fact = getattr(error, "stage_timeout", None)
        self.assertIsNotNone(fact, "original timeout was lost during exceptional unwind")
        self.assertEqual(stage.kind, fact["kind"])
        self.assertTrue(fact["reason"])
        self.assertTrue(stage.rows)
        self.assertIn("cleanup", stage.trace)
        self.assertLess(stage.trace.index("capture"), stage.trace.index("poll"))

    def test_idle_decision_survives_same_authentication_error(self):
        stage = ControlledStage(self)
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait()
        self.assertIs(stage.original, caught.exception)
        self.assertEqual("original held-root authentication refused", str(caught.exception))
        self.assert_timeout(stage, caught.exception)
        original_fact = dict(caught.exception.stage_timeout)
        stage.monitor.timeout = {"kind": "tool", "reason": "foreign monitor contents"}
        self.assertTrue(processes.retain_stage_timeout(caught.exception, stage.monitor))
        self.assertEqual(original_fact, stage.monitor.timeout)
        self.assertIsNot(caught.exception.stage_timeout, stage.monitor.timeout)
        stage.monitor.timeout["reason"] = "later monitor mutation"
        self.assertEqual(original_fact, caught.exception.stage_timeout)
        self.assertTrue(processes.retain_stage_timeout(caught.exception, None))
        self.assertEqual(original_fact, caught.exception.stage_timeout)

    def test_stage_deadline_decision_survives_same_error(self):
        stage = ControlledStage(self, kind="stage")
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait()
        self.assertIs(stage.original, caught.exception)
        self.assert_timeout(stage, caught.exception)

    def test_tool_deadline_decision_survives_same_error(self):
        stage = ControlledStage(self, kind="tool")
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait()
        self.assertIs(stage.original, caught.exception)
        self.assert_timeout(stage, caught.exception)

    def test_cleanup_failure_keeps_its_error_and_original_timeout(self):
        cleanup = processes.ProcessError("original cleanup remained uncertain")
        stage = ControlledStage(self, cleanup_error=cleanup)
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait()
        self.assertIs(cleanup, caught.exception)
        self.assertIs(stage.original, caught.exception.__context__)
        self.assertEqual("idle", getattr(caught.exception, "stage_timeout", {}).get("kind"))
        self.assertEqual("original cleanup remained uncertain", str(caught.exception))

    def test_other_cleanup_exception_does_not_mutate_suppressed_body_error(self):
        cleanup = ValueError("different cleanup failure")
        stage = ControlledStage(self, cleanup_error=cleanup)
        with stage.bound(), self.assertRaises(ValueError) as caught:
            stage.wait()
        self.assertIs(cleanup, caught.exception)
        self.assertIs(stage.original, caught.exception.__context__)
        self.assertIsNone(getattr(stage.original, "stage_timeout", None))

    def test_unfired_watchdog_does_not_trust_monitor_timeout_or_error_text(self):
        stage = ControlledStage(self)
        stage.monitor.timeout = {"kind": "idle", "reason": "caller-built claim"}
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait(cancel=False)
        self.assertIs(stage.original, caught.exception)
        self.assertIsNone(getattr(caught.exception, "stage_timeout", None))
        before = dict(stage.monitor.timeout)
        self.assertFalse(processes.retain_stage_timeout(caught.exception, stage.monitor))
        self.assertEqual(before, stage.monitor.timeout)
        self.assertFalse(processes.retain_stage_timeout(caught.exception, None))

    def test_cancelled_original_release_guard_never_writes_target_token(self):
        stage = ControlledStage(self)
        with stage.bound(), self.assertRaisesRegex(processes.ProcessError, "ended before release") as caught:
            stage.wait(release=True)
        self.assert_timeout(stage, caught.exception)

    def test_early_observer_still_runs_during_root_capture_without_polling(self):
        stage = ControlledStage(self, early_observer=True)
        with stage.bound(), self.assertRaises(processes.ProcessError) as caught:
            stage.wait()
        self.assertLess(stage.trace.index("observe"), stage.trace.index("capture"))
        self.assert_timeout(stage, caught.exception)


class CallerTimeoutTests(unittest.TestCase):
    setUp = caller_fixture.RetrofitTest.setUp

    def call(self, *, cleanup=False, cancel=True):
        stage = ControlledStage(self)
        self.settings["limits"] = {
            "stage_timeout_seconds": 900, "idle_timeout_seconds": 300, "tool_timeout_seconds": 600,
        }
        original_wait = processes.wait_for_stage

        @contextlib.contextmanager
        def launch(command, *, checkpoint, **options):
            checkpoint({"owner": {}, "provider": {"pid": stage.child.pid}})
            yield stage.child
            if cleanup:
                raise runner.supervision.SupervisionError("keeper cleanup is separately uncertain")

        def wait(child, *_args, **_options):
            self.assertIs(stage.child, child)
            # Exercise the actual modified producer, not a caller-built timeout
            # attribute, inside the controlled transport's held-root fault.
            with patch.object(processes, "wait_for_stage", original_wait), stage.bound():
                return stage.wait(cancel=cancel)

        with (
            patch.object(runner.supervision, "launch", launch),
            patch.object(runner.supervision, "receipt", return_value={
                "phase": "uncertain" if cleanup else "stopped",
                "cause": "provider_stopped", "cleanup_error": "uncollected" if cleanup else None,
            }),
            patch.object(runner.support, "snapshot", return_value={"head": "h", "files": {}, "revision": "r"}),
            patch.object(processes, "preflight"),
            patch.object(processes, "wait_for_stage", side_effect=wait),
            patch.object(runner, "ActivityMonitor", return_value=stage.monitor),
            patch.object(runner, "load_stage_report") as report,
        ):
            with self.assertRaises(runner.support.Paused) as caught:
                runner.run_role(
                    role="terra", prompt="controlled complete handoff", sandbox="workspace-write",
                    workspace=self.root, run_dir=self.run, state=self.state,
                    schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                    model="controlled/model", allow_write=True, dry_run=False,
                )
        self.assertEqual("PAUSED_PROCESS_CLEANUP", caught.exception.status)
        report.assert_not_called()
        active = runner.support.read(self.run / "state.json")["active_stage"]
        self.assertTrue(active["accounted"])
        self.assertFalse(runner.stage_completed(self.state, active))
        self.assertEqual((900, 300, 600), tuple(active[k] for k in (
            "stage_timeout_seconds", "idle_timeout_seconds", "tool_timeout_seconds",
        )))
        return active, str(caught.exception)

    def test_caller_retains_timeout_and_authentication_hold(self):
        active, reason = self.call()
        self.assertTrue(active["timed_out"], "caller lost the original producer timeout")
        self.assertEqual("idle", active["timeout_kind"])
        self.assertIn("inactivity limit", active["timeout_reason"])
        self.assertIn("original held-root authentication refused", reason)

    def test_caller_preserves_separate_cleanup_failure_with_timeout(self):
        active, reason = self.call(cleanup=True)
        self.assertTrue(active["timed_out"])
        self.assertEqual("idle", active["timeout_kind"])
        self.assertIn("original held-root authentication refused", reason)
        self.assertIn("keeper cleanup is separately uncertain", reason)

    def test_caller_does_not_classify_unrelated_authentication_error_as_timeout(self):
        active, reason = self.call(cancel=False)
        self.assertFalse(active["timed_out"])
        self.assertNotIn("timeout_kind", active)
        self.assertIn("original held-root authentication refused", reason)
