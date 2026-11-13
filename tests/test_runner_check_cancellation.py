"""Stop now uses the existing check owner, never a proof or a different run's request."""

import json
import os
import select
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_command_receipt as receipts
import autocode_command_supervision as commands
import autocode_interventions as interventions
import autocode_process as processes
import autocode_runner_check as checks
import autocode_util as util

from tests import test_runner_check as fixture


class RunnerCancellationTests(unittest.TestCase):
    setUp = fixture.RunnerCheckTests.setUp
    _persist = fixture.RunnerCheckTests._persist
    _hold = fixture.RunnerCheckTests._hold

    def request(self, *, request_id="stop-check", now=True, kind="pause"):
        return interventions.submit(
            self.project.root,
            self.run,
            request_id=request_id,
            kind=kind,
            text="feedback" if kind == "feedback" else "",
            now=now,
        )["receipt"]

    def test_only_a_fresh_stop_now_for_this_run_cancels_and_context_is_reset(self):
        original = commands.CANCELLATION.get()
        self.request(request_id="boundary", now=False)
        self.request(request_id="feedback", now=False, kind="feedback")
        stale = self.request(request_id="already-applied")
        self.state["applied_interventions"] = [{**stale, "applied_at": util.now()}]
        with checks.track(self.state, self.run, "regression_proof", "Checking", self._persist):
            callback = commands.CANCELLATION.get()
            self.assertIsNone(callback())
            request = self.request(request_id="fresh")
            self.assertEqual(request, callback())
            (self.run / interventions.INBOX_NAME).write_text("invalid JSON")
            self.assertIsNone(callback())
        self.assertIs(original, commands.CANCELLATION.get())
        other = self.run.with_name("other-run")
        other.mkdir()
        with checks.track(self.state, other, "regression_proof", "Other check", self._persist):
            self.assertIsNone(commands.CANCELLATION.get()())
        self.assertIs(original, commands.CANCELLATION.get())

    def test_cleanup_precedes_exact_once_application_and_no_following_command_runs(self):
        request = self.request()
        with self.assertRaises(util.Paused) as paused:
            with checks.track(self.state, self.run, "regression_proof", "Checking", self._persist):
                commands.run("exit 0", self.project.root, self.run / "never.log", timeout=5, env=os.environ)
                self.fail("Stop now allowed another check")
        self.assertEqual("PAUSED_INTERVENTION", paused.exception.status)
        self.assertNotIn("active_runner_check", self.state)
        self.assertEqual([request["id"]], [row["id"] for row in self.state["applied_interventions"]])
        retry = interventions.submit(
            self.project.root, self.run, request_id=request["id"], kind="pause", text="", now=True
        )
        self.assertEqual("already_applied", retry["consumer"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual([], interventions.pending(self.run))
        self.assertFalse((self.run / "never.log").exists())

    def test_changed_observed_request_cannot_be_acknowledged(self):
        request = self.request()
        with self.assertRaises(util.Paused) as paused:
            with checks.track(self.state, self.run, "regression_proof", "Checking", self._persist):
                (self.run / interventions.INBOX_NAME).write_text(json.dumps({"version": 1, "requests": []}))
                raise commands.CheckCancelled(request)
        self.assertEqual("PAUSED_INTERVENTION_PENDING", paused.exception.status)
        self.assertNotIn("applied_interventions", self.state)
        self.assertIsNone(commands.CANCELLATION.get())

    def test_uncertain_cleanup_retains_owner_and_unacknowledged_request(self):
        request = self.request()
        with self.assertRaises(receipts.OwnershipUncertain):
            with checks.track(self.state, self.run, "regression_proof", "Checking", self._persist):
                metadata = self._hold()
                commands.CHECKPOINT.get()("held check", self.run / "held.log", metadata)
                with patch.object(commands, "reconcile", side_effect=receipts.OwnershipUncertain("cleanup unknown")):
                    raise commands.CheckCancelled(request)
        self.assertEqual(metadata, self.state["active_runner_check"]["supervision"])
        self.assertNotIn("applied_interventions", self.state)
        self.assertEqual([request], interventions.pending(self.run))
        self.assertIsNone(commands.CANCELLATION.get())

    def test_uncertain_cancellation_cannot_retire_even_a_dead_inventory_as_an_audit_event(self):
        request = self.request()
        with self.assertRaises(receipts.OwnershipUncertain):
            with checks.track(self.state, self.run, "regression_proof", "Checking", self._persist):
                metadata = self._hold()
                commands.CHECKPOINT.get()("held check", self.run / "held.log", metadata)
                uncertain = receipts.OwnershipUncertain("cancelled owner cleanup is uncertain")
                uncertain.check_cancelled = True
                with patch.object(commands, "processes_absent", return_value=True):
                    raise uncertain
        self.assertEqual(metadata, self.state["active_runner_check"]["supervision"])
        self.assertNotIn("applied_interventions", self.state)
        self.assertNotIn("user_events", self.state)
        self.assertEqual([request], interventions.pending(self.run))


@unittest.skipUnless(sys.platform == "linux", "native ownership checks require supported Linux")
class OwnedCancellationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-cancellation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_owned_command_cancellation_retains_nonpass_output_and_reaps_descendants(self):
        ready, gate = self.root / "ready", self.root / "gate"
        for path in (ready, gate):
            os.mkfifo(path)
        ready_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
        gate_fd = os.open(gate, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, ready_fd)
        self.addCleanup(os.close, gate_fd)
        completed = self.root / "completed"
        child = "from pathlib import Path; Path(" + repr(str(gate)) + ").open('rb').read(1)"
        source = (
            "import json,os,subprocess,sys; from pathlib import Path; "
            f"child=subprocess.Popen([sys.executable,'-c',{child!r}]); "
            f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(),child.pid])); "
            "print('retained partial output',flush=True); child.wait(); "
            f"Path({str(completed)!r}).touch()"
        )
        observed = []

        def cancel():
            if select.select([ready_fd], [], [], 0)[0]:
                observed.extend(json.loads(os.read(ready_fd, 4096)))
                return {"id": "observed-stop"}
            return None

        token = commands.CANCELLATION.set(cancel)
        try:
            with self.assertRaises(commands.CheckCancelled) as stopped:
                commands.run(
                    "exec " + shlex.join([sys.executable, "-u", "-c", source]),
                    self.root,
                    self.root / "check.log",
                    timeout=30,
                    env=dict(os.environ),
                )
        finally:
            commands.CANCELLATION.reset(token)
        self.assertEqual({"id": "observed-stop"}, stopped.exception.request)
        self.assertEqual(2, len(observed))
        (result_path,) = self.root.glob("command-supervision/*/result.json")
        result = json.loads(result_path.read_text())
        self.assertTrue(result["interrupted"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertIn("retained partial output", result["tail"])
        self.assertFalse(receipts.completed(result))
        self.assertFalse(completed.exists())
        final = commands.reconcile(result["supervision"])
        self.assertEqual(set(observed), {row["pid"] for row in final["processes"]} & set(observed))
        self.assertFalse(processes.live_processes(final["processes"]))

    def test_wait_slices_keep_the_original_deadline_and_no_callback_waits_once(self):
        child = Mock()
        child.wait.side_effect = subprocess.TimeoutExpired("owned command", 1)
        with patch.object(commands.time, "monotonic", side_effect=[10, 11, 11, 12, 12, 12.5]):
            with self.assertRaises(subprocess.TimeoutExpired):
                commands._wait(child, 12.5, lambda: None)
        self.assertEqual([1, 1, 0.5], [call.kwargs["timeout"] for call in child.wait.call_args_list])
        child.reset_mock(side_effect=True)
        child.wait.return_value = 7
        with patch.object(commands.time, "monotonic", return_value=10):
            self.assertEqual(7, commands._wait(child, 20, None))
        child.wait.assert_called_once_with(timeout=10)

    def test_stop_without_activity_uses_the_original_tree_and_on_root_cleanup(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        completed = self.root / "completed"
        source = f"import os; from pathlib import Path; os.read({read_fd},1); Path({str(completed)!r}).touch()"
        child = subprocess.Popen([sys.executable, "-c", source], pass_fds=(read_fd,), start_new_session=True)
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        root_seen = threading.Event()
        roots, owned = [], []

        def on_root(tree, release):
            self.assertEqual(child.pid, tree.pid)
            self.assertIn(child.pid, tree.known)
            self.assertTrue(callable(release))
            roots.append(tree)
            root_seen.set()

        code, stopped = processes.wait_for_stage(
            child, 30, lambda rows: owned.extend(rows), on_root=on_root, stop_requested=root_seen.is_set
        )
        self.assertTrue(stopped)
        self.assertNotEqual(0, code)
        self.assertEqual(1, len(roots))
        self.assertFalse(completed.exists())
        self.assertFalse(processes.live_processes(owned))
