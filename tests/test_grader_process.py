"""A grading result is publishable only after its owned processes stop."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_grader_process as grader
import autocode_process as processes


class GraderExitObservationTests(unittest.TestCase):
    def observed_process(self, status, birth=1.0):
        process = Mock()
        process.oneshot.return_value = nullcontext()
        process.create_time.return_value = birth
        process._ident = (77, birth)
        process.ppid.return_value = grader.os.getpid()
        process.status.return_value = status
        return process

    def test_exit_between_status_and_group_read_uses_fresh_zombie_identity(self):
        before = self.observed_process("running")
        after = self.observed_process(grader.psutil.STATUS_ZOMBIE)
        with (
            patch.object(grader.psutil, "Process", side_effect=[before, after]),
            patch.object(grader.os, "getpgid", side_effect=ProcessLookupError),
        ):
            row = grader._leader(SimpleNamespace(pid=77))
        self.assertEqual(grader.psutil.STATUS_ZOMBIE, row["state"])
        self.assertEqual(77, row["group"])

    def test_unavailable_group_does_not_authorize_a_different_birth_identity(self):
        before = self.observed_process("running")
        after = self.observed_process(grader.psutil.STATUS_ZOMBIE, birth=2.0)
        with (
            patch.object(grader.psutil, "Process", side_effect=[before, after]),
            patch.object(grader.os, "getpgid", side_effect=ProcessLookupError),
        ):
            with self.assertRaisesRegex(processes.ProcessError, "zombie identity"):
                grader._leader(SimpleNamespace(pid=77))

    def test_exit_transition_waits_for_observed_zombie_not_an_unknown_status(self):
        observations = [self.observed_process(status) for status in ("running", "unknown", grader.psutil.STATUS_ZOMBIE)]
        with (
            patch.object(grader.psutil, "Process", side_effect=observations),
            patch.object(grader.os, "getpgid", side_effect=ProcessLookupError),
            patch.object(grader.time, "sleep"),
        ):
            row = grader._leader(SimpleNamespace(pid=77))
        self.assertEqual(grader.psutil.STATUS_ZOMBIE, row["state"])


class GraderProcessTests(unittest.TestCase):
    def test_first_deadline_clock_interrupt_cleans_up_after_initial_sample(self):
        child = Mock(pid=77)
        root = {"pid": 77, "group": 77}
        tree = Mock(known={})
        events = []
        tree.sample.side_effect = lambda: events.append("sample")
        tree.stop.side_effect = lambda child: events.append("stop")

        def interrupt():
            events.append("clock")
            raise KeyboardInterrupt

        with (
            patch.object(processes, "ProcessTree", return_value=tree),
            patch.object(grader, "_leader", return_value=root),
            patch.object(processes, "identity", return_value=root),
            patch.object(grader, "_capture_group") as capture,
            patch.object(grader.time, "monotonic", side_effect=interrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                grader.wait(child, 10)
        self.assertEqual(["sample", "clock", "stop"], events)
        capture.assert_called_once_with(tree, child, root)
        child.wait.assert_not_called()

    def test_bootstrap_observation_failures_stop_without_publishing_result(self):
        for stage in ("leader", "sample", "capture"):
            with self.subTest(stage=stage):
                child = Mock(pid=77)
                root = {"pid": 77, "group": 77, "state": grader.psutil.STATUS_ZOMBIE}
                tree = Mock(known={})
                failure = processes.ProcessError("bootstrap ownership uncertain")
                if stage == "sample":
                    tree.sample.side_effect = failure
                with (
                    patch.object(processes, "ProcessTree", return_value=tree),
                    patch.object(
                        grader, "_leader", side_effect=failure if stage == "leader" else None, return_value=root
                    ),
                    patch.object(processes, "identity", return_value=root),
                    patch.object(processes, "matches", return_value=True),
                    patch.object(grader, "_capture_group", side_effect=failure if stage == "capture" else None),
                ):
                    with self.assertRaisesRegex(processes.ProcessError, "ownership uncertain"):
                        grader.wait(child, 10)
                tree.stop.assert_called_once_with(child)
                child.wait.assert_not_called()

    def test_first_clock_sigterm_stops_ready_detached_helper_and_preserves_sentinel(self):
        ready_read, ready_write = os.pipe()
        script = (
            "import os,subprocess,sys\n"
            "helper=subprocess.Popen([sys.executable,'-c',"
            "'import os; r,w=os.pipe(); os.read(r,1)'],start_new_session=True)\n"
            "os.write(int(sys.argv[1]),str(helper.pid).encode()+b'\\n')\n"
            "r,w=os.pipe(); os.read(r,1)\n"
        )
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        child = None
        cleanup = None
        before = signal.getsignal(signal.SIGTERM)
        try:
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(ready_write)], pass_fds=(ready_write,), start_new_session=True
            )
            os.close(ready_write)
            ready_write = None
            helper_pid = int(os.read(ready_read, 64))
            cleanup = processes.ProcessTree(child.pid, lambda rows: None)
            cleanup.capture_root()
            cleanup.sample()
            real_time = grader.time
            clock = Mock(wraps=real_time)

            def interrupt():
                signal.raise_signal(signal.SIGTERM)
                return real_time.monotonic()

            clock.monotonic.side_effect = interrupt
            with processes.interruption_handler(), patch.object(grader, "time", clock):
                with self.assertRaises(KeyboardInterrupt):
                    grader.wait(child, 10)
            self.assertEqual(1, clock.monotonic.call_count)
            self.assertIn(helper_pid, cleanup.known)
            self.assertEqual([], processes.live_processes(list(cleanup.known.values())))
            self.assertIsNotNone(child.returncode)
            self.assertIsNone(sentinel.poll())
            self.assertEqual(before, signal.getsignal(signal.SIGTERM))
        finally:
            os.close(ready_read)
            if ready_write is not None:
                os.close(ready_write)
            if cleanup is not None:
                cleanup.stop(child)
            elif child is not None:
                grader.wait(child, 0)
            sentinel.stdin.close()
            sentinel.wait(timeout=5)

    def fixture(self, root):
        # Children block on their own pipe, so parent exit cannot release them.
        code = (
            "import subprocess,sys,json\nfrom pathlib import Path\n"
            "children=[subprocess.Popen([sys.executable,'-c',"
            f"'import os; r,w=os.pipe(); os.read(r,1)',{str(root)!r}]) for _ in range(4)]\n"
            f"Path({str(root / 'children.json')!r}).write_text(json.dumps([p.pid for p in children]))\n"
        )
        child = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
        cleanup = processes.ProcessTree(child.pid, lambda rows: None)
        cleanup.capture_root()
        return child, cleanup

    def test_fast_parent_exit_waits_for_children_and_preserves_unrelated_process(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child, cleanup = self.fixture(root)
                try:
                    code, expired, receipt = grader.wait(child, 10)
                    self.assertEqual(0, code)
                    self.assertFalse(expired)
                    self.assertTrue(receipt["checked"])
                    self.assertEqual([], processes.live_processes(receipt["owned"]))
                    expected = set(json.loads((root / "children.json").read_text()))
                    self.assertTrue(expected <= {row["pid"] for row in receipt["owned"]})
                    self.assertIsNone(sentinel.poll())
                finally:
                    cleanup.stop(child)
        finally:
            sentinel.stdin.close()
            sentinel.wait(timeout=5)

    def test_incomplete_cleanup_cannot_return_a_grading_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, cleanup = self.fixture(Path(tmp))
            try:
                # Negative control: a cleanup implementation that returns while
                # workers still exist must raise, even after a successful root.
                with patch.object(processes.ProcessTree, "stop", return_value=None):
                    with self.assertRaisesRegex(processes.ProcessError, "live owned processes") as rejected:
                        grader.wait(child, 10)
                    cleanup.known.update({row["pid"]: row for row in rejected.exception.processes})
            finally:
                cleanup.stop(child)
