"""Two runs in one checkout: only one run's agents work in it at a time."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_checkout_lock as checkout_lock
import autocode_util as util

from . import test_subprocess


class ExclusiveTests(unittest.TestCase):
    def test_a_second_holder_is_refused_and_told_who_holds_it(self):
        with tempfile.TemporaryDirectory() as workspace:
            with checkout_lock.exclusive(workspace, "/runs/first"):
                self.assertEqual("/runs/first", checkout_lock.holder(workspace)["run_dir"])
                with self.assertRaises(checkout_lock.CheckoutBusy) as busy:
                    with checkout_lock.exclusive(workspace, "/runs/second"):
                        self.fail("entered a held checkout")
                self.assertEqual("PAUSED_WORKSPACE_BUSY", busy.exception.status)
                self.assertIn("/runs/first", str(busy.exception))
            self.assertEqual({}, checkout_lock.holder(workspace))
            with checkout_lock.exclusive(workspace, "/runs/second"):
                pass


class InPlaceRunTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_a_run_waits_while_another_run_works_in_the_checkout(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        other = self.project / ".autocode" / "runs" / "other-run"
        with checkout_lock.exclusive(self.project, other):
            result = self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
            self.assertIn(
                f"Another AutoCode run is working in this checkout ({other}); nothing was changed", result.stderr
            )
            self.assertIn("without --in-place", result.stderr)
            run, state = self.saved()
            self.assertEqual("RUNNING", state["status"], "a busy checkout changes nothing in the waiting run")
            self.assertIsNone(state["base_commit"], "the other run's unfinished files are not where this run starts")
            (self.project / "notes.txt").write_text("the other run's work\n")
            self.assertEqual(
                [],
                [row for row in state["stages"] if not row.get("runner_owned")],
                "no agent may run while another run holds the checkout",
            )
            self.assertFalse((self.project / "greet.py").exists())
            status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)
            self.assertEqual("RUNNING", status["status"])
            self.assertEqual("ready", status["view"]["controller"]["kind"], status)
            self.assertEqual(2, status["view"]["controller"]["receipt"]["exit_code"])
        # The same kind of command, once the other run has stopped, simply continues.
        self.launch(["--run-dir", str(run), "--chat"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(
            ["notes.txt"],
            subprocess.run(
                ["git", "ls-tree", "--name-only", state["base_commit"]],
                cwd=self.project,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split(),
            "the run starts from the checkout as it first holds it",
        )
        self.assertEqual({}, checkout_lock.holder(self.project))


class DurableSinkTests(unittest.TestCase):
    def test_observer_runs_after_both_fsyncs_and_receives_exact_sink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            value = {"unicode": "\u03bb", "rows": [True, None, 1.5]}
            order, observed = [], []
            real_fsync = os.fsync

            def fsync(fd):
                real_fsync(fd)
                order.append("fsync")

            def observer(**receipt):
                order.append("observer")
                self.assertEqual(value, receipt["value"])
                self.assertEqual(path.read_bytes(), receipt["serialized_bytes"])
                sink = path.stat()
                self.assertEqual(
                    {
                        "path": str(path),
                        "device": sink.st_dev,
                        "inode": sink.st_ino,
                        "bytes": sink.st_size,
                        "mode": sink.st_mode & 0o7777,
                    },
                    receipt["sink_identity"],
                )
                observed.append(receipt)

            with patch.object(util.os, "fsync", side_effect=fsync):
                util.atomic_json(path, value, after_commit=observer)
            self.assertEqual(["fsync", "fsync", "observer"], order)
            self.assertEqual(1, len(observed))
            self.assertEqual((json.dumps(value, indent=2, sort_keys=True) + "\n").encode(), path.read_bytes())

    def test_failed_directory_fsync_never_emits_commit_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            observed = []
            with patch.object(util.os, "fsync", side_effect=[None, OSError("directory fsync failed")]):
                with self.assertRaisesRegex(OSError, "directory fsync failed"):
                    util.atomic_json(path, {"new": True}, after_commit=lambda **row: observed.append(row))
            self.assertEqual([], observed)
            self.assertEqual({"new": True}, json.loads(path.read_text()))

    def test_failed_replacement_preserves_previous_value_and_no_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            util.atomic_json(path, {"old": True})
            previous = path.read_bytes()
            observed = []
            with patch.object(util.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    util.atomic_json(path, {"new": True}, after_commit=lambda **row: observed.append(row))
            self.assertEqual(previous, path.read_bytes())
            self.assertEqual([], observed)
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_observer_error_does_not_imply_that_persistence_rolled_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"

            def observer(**receipt):
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "publication remains pending")

            with self.assertRaisesRegex(util.Paused, "publication remains pending"):
                util.atomic_json(path, {"new": True}, after_commit=observer)
            self.assertEqual({"new": True}, json.loads(path.read_text()))

    def test_invalid_observer_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            with self.assertRaisesRegex(TypeError, "after_commit must be callable"):
                util.atomic_json(path, {"new": True}, after_commit=True)
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_receipt_value_is_sealed_before_mutation_during_file_fsync(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            value = {"nested": {"answer": "original"}}
            observed = []
            real_fsync = os.fsync
            calls = 0

            def fsync(fd):
                nonlocal calls
                real_fsync(fd)
                calls += 1
                if calls == 1:
                    value["nested"]["answer"] = "later parent mutation"

            with patch.object(util.os, "fsync", side_effect=fsync):
                util.atomic_json(path, value, after_commit=lambda **receipt: observed.append(receipt))
            self.assertEqual({"nested": {"answer": "original"}}, observed[0]["value"])
            self.assertEqual(json.loads(path.read_bytes()), observed[0]["value"])
            self.assertEqual("later parent mutation", value["nested"]["answer"])
            self.assertIsNot(value["nested"], observed[0]["value"]["nested"])

    def test_receipt_uses_json_normalized_value_and_callback_cannot_mutate_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "delivery.json"
            value = {1: ("tuple", {"answer": "original"})}

            def observer(**receipt):
                self.assertEqual({"1": ["tuple", {"answer": "original"}]}, receipt["value"])
                self.assertEqual(json.loads(receipt["serialized_bytes"]), receipt["value"])
                receipt["value"]["1"][1]["answer"] = "observer mutation"

            util.atomic_json(path, value, after_commit=observer)
            self.assertEqual("original", value[1][1]["answer"])
            self.assertEqual({"1": ["tuple", {"answer": "original"}]}, json.loads(path.read_bytes()))

    def test_original_stop_and_status_writers_forward_final_normalized_sink(self):
        import autocode_run_records as records
        import autocode_status as status
        import autocode_stop as stop

        for stopped in (False, True):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                value = {
                    "status": "RUNNING",
                    "iteration": 1,
                    "next_stage": "terra",
                    "current_task": {"id": "owned-task", "objective": "fixture"},
                }
                if stopped:
                    value["applied_interventions"] = [{"kind": "stop"}]
                observed = []
                writer = stop.state_writer(records.write_json, status.persist)
                writer(path, value, after_commit=lambda **receipt: observed.append(receipt))
                self.assertEqual(1, len(observed))
                self.assertEqual(json.loads(path.read_bytes()), observed[0]["value"])
                self.assertEqual(json.loads(path.read_bytes()), value)
                self.assertTrue(observed[0]["value"]["intervention_capability"]["supports_stop"])
                self.assertEqual("owned-task", observed[0]["value"]["execution_checkpoints"]["task_id"])
                if stopped:
                    self.assertEqual(stop.STOP_STATUS, observed[0]["value"]["status"])
                    self.assertEqual(stop.STOP_REASON, observed[0]["value"]["stop_reason"])

    def test_failure_after_durable_sink_is_not_a_successful_parent_boundary(self):
        import autocode_activity_log as activity_log
        import autocode_status as status

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            value = {"status": "RUNNING", "iteration": 1}
            observed = []
            with patch.object(activity_log, "record", side_effect=OSError("post-sink log failed")):
                with self.assertRaisesRegex(OSError, "post-sink log failed"):
                    status.persist(path, value, after_commit=lambda **receipt: observed.append(receipt))
            self.assertEqual(1, len(observed), "a sink ACK alone cannot imply whole-boundary success")
            self.assertEqual(json.loads(path.read_bytes()), observed[0]["value"])


class LeaseHandleTests(unittest.TestCase):
    def test_run_handle_is_the_actual_held_descriptor_and_closes_after_release(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            with util.run_lock(run) as handle:
                sink = (run / "writer.lock").stat()
                held = os.fstat(handle.fileno())
                self.assertEqual((sink.st_dev, sink.st_ino), (held.st_dev, held.st_ino))
                with self.assertRaises(util.Paused) as stopped:
                    with util.run_lock(run):
                        self.fail("entered held run")
                self.assertEqual("PAUSED_RUN_BUSY", stopped.exception.status)
                self.assertFalse(handle.closed)
            self.assertTrue(handle.closed)

    def test_checkout_handle_is_inherited_by_child_options_and_release_stays_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with checkout_lock.exclusive(workspace, "/owned/run") as handle:
                sink = checkout_lock.lock_path(workspace).stat()
                held = os.fstat(handle.fileno())
                self.assertEqual((sink.st_dev, sink.st_ino), (held.st_dev, held.st_ino))
                self.assertIn(handle.fileno(), checkout_lock.child_options(workspace, {})["pass_fds"])
                with self.assertRaises(checkout_lock.CheckoutBusy):
                    with checkout_lock.exclusive(workspace, "/another/run"):
                        self.fail("entered held checkout")
            self.assertTrue(handle.closed)
            with checkout_lock.exclusive(workspace, "/another/run") as next_handle:
                self.assertFalse(next_handle.closed)


if __name__ == "__main__":
    unittest.main()
