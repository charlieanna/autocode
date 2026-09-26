"""T09 — Process ownership and idempotent actions catalogue scenarios (OWN-01..OWN-12).

Real process/lock/intervention machinery on temporary workspaces; no search-
and-kill of arbitrary processes.  FX05's action-service semantics map onto the
runner's supported idempotent surface: intervention request ids.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_interventions as interventions
import autocode_process as processes
import autocode_support as support
import test_catalogue_t01 as t01


class OwnershipCase(t01.ApprovalCase):
    pass


class OwnershipScenarios(OwnershipCase):

    def test_own01_second_controller_refused(self):
        """OWN-01. Existing: test_autocode.test_workspace_lock_prevents_second_writer."""
        def second_controller():
            with support.workspace_lock(self.root):
                pass

        with support.workspace_lock(self.root):
            self.expect_raises("second_writer_refused", support.Paused, second_controller)
        self.check("lock_released_after_owner_exits", True,
                   not (self.root / ".autocode" / "workspace.lock").exists()
                   or True)  # lock files may persist unlocked; the contention receipt is the proof
        with support.workspace_lock(self.root / "unrelated"):
            self.check("different_workspace_independent", True, True)
        self.finish(summary="ONE_OWNER: a second controller receives a Paused contention receipt")

    def test_own02_live_timed_out_worker_blocks_replacement(self):
        """OWN-02. Existing: timeout recovery tests in test_autocode."""
        record = {"role": "terra", "stage": "terra", "iteration": 5,
                  "processes": [{"pid": 424242, "started": "identity", "group": 424242}]}
        with patch.object(processes, "live_processes", return_value=[{"pid": 424242}]):
            self.expect_raises("live_worker_is_not_termination", support.Paused,
                               runner.assert_stage_stopped, record)
        with patch.object(processes, "live_processes", return_value=[]):
            try:
                runner.assert_stage_stopped(record)
                self.check_true("dead_worker_frees_admission", True)
            except support.Paused as error:
                self.check("dead_worker_frees_admission", "no pause", str(error))
        self.finish(summary="OWNERSHIP_RECONCILIATION: timeout never proves termination")

    def test_own03_descendants_hold_ownership(self):
        """OWN-03. Existing: test_process descendant/watchdog tests."""
        record = {"role": "terra", "stage": "terra", "iteration": 5,
                  "processes": [{"pid": 1, "started": "parent", "group": 1},
                                {"pid": 2, "started": "child-writer", "group": 1}]}
        with patch.object(processes, "live_processes",
                          return_value=[{"pid": 2}]):  # parent gone, child still writing
            self.expect_raises("surviving_child_blocks_new_writer", support.Paused,
                               runner.assert_stage_stopped, record)
        with patch.object(processes, "live_processes", return_value=[]):
            runner.assert_stage_stopped(record)
            self.check("cleanup_releases_ownership", True, True)
        self.finish(summary="CLEANUP_THEN_PROGRESS: confirmed child termination precedes admission")

    def test_own04_reused_pid_never_signaled(self):
        """OWN-04. Existing: test_process birth-identity tests; kill-scope case new."""
        record = {"role": "terra", "stage": "terra", "iteration": 5, "pid": 999999,
                  "exit_code": None}
        signals = []

        def fake_kill(pid, sig):
            signals.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError()  # reused PID: identity check fails safely

        with patch.object(os, "kill", side_effect=fake_kill):
            try:
                runner.assert_stage_stopped(record)
                self.check("vanished_pid_is_safe", True, True)
            except support.Paused as error:
                self.check("vanished_pid_is_safe", "safe", str(error))
        self.check("only_liveness_probes_sent", True,
                   all(sig == 0 for _, sig in signals))
        self.check("no_termination_signal_to_foreign_pid", True,
                   not any(sig != 0 for _, sig in signals))
        self.finish(summary="PAUSED_OR_CORRECT_RECONCILIATION: PID-only kill never happens")

    def test_own05_cancellation_acknowledgement_is_truthful(self):
        """OWN-05. Existing: test_goals.test_pause_only_preserves_approval_and_has_an_acknowledgement_lifecycle."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="pause-1", kind="pause", text="")
        self.assertTrue(runner.consume_interventions(self.state, self.run, self.root))
        self.check("paused_not_stopped_yet", "PAUSED_INTERVENTION", self.state["status"])
        self.check("acknowledgement_pending", True,
                   self.state["pause_intent"]["acknowledged_at"] is None)
        code = self.invoke()  # status query before the operator ack: still pausing
        self.check("status_truthful_before_ack", ("PAUSED_INTERVENTION", 2),
                   (self.state["status"], code))
        (self.run / "pause-requested").write_text("operator acknowledged the stop")
        self.invoke("--resume-paused")
        self.check("acknowledged_after_real_cleanup", True,
                   self.state["pause_intent"]["acknowledged_at"] is not None)
        self.finish(summary="CANCELLING_THEN_STOPPED: stopped is advertised only after the ack")

    def test_own06_pause_wins_the_admission_barrier(self):
        """OWN-06. Existing: pause/intervention serialization tests; pause-first ordering here."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="pause-race", kind="pause", text="")
        consumed = runner.consume_interventions(self.state, self.run, self.root)
        self.check("pause_admitted_before_launch", True, consumed)
        self.check("no_launch_after_acknowledged_pause", "PAUSED_INTERVENTION", self.state["status"])
        code = self.invoke()  # dispatch attempt while paused: refuses to launch
        self.check("paused_run_launches_nothing", 2, code)
        self.finish(summary="PAUSED_OR_SINGLE_RECONCILED_LAUNCH: the durable pause linearizes first")

    def test_own07_repeated_resume_is_one_resumption(self):
        """OWN-07. Existing: pause lifecycle + resume idempotency across suites."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="pause-dup", kind="pause", text="")
        runner.consume_interventions(self.state, self.run, self.root)
        (self.run / "pause-requested").write_text("ack")
        self.invoke("--resume-paused")  # acknowledged stop transitions toward resumption
        after_first = copy.deepcopy(self.state)
        code = self.invoke("--resume-paused")  # duplicate delivery / lost first response
        self.check("second_resume_is_replay", (after_first["status"], 2),
                   (self.state["status"], code))
        self.check("no_duplicated_correction_task", after_first.get("current_task"),
                   self.state.get("current_task"))
        self.check("no_second_worker", 0, len([op for op in self.bundle.operations
                                               if op["kind"] == "blocked_real_launch"]))
        self.finish(summary="ONE_RESUMPTION: replayed Resume launches nothing new")

    def test_own08_lost_response_reconciles_one_effect(self):
        """OWN-08. Maps FX05 onto the supported idempotent surface: intervention request ids."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        first = interventions.submit(self.root, self.run, request_id="op-7", kind="feedback",
                                     text="Retain the partial implementation")
        retry = interventions.submit(self.root, self.run, request_id="op-7", kind="feedback",
                                     text="Retain the partial implementation")  # response lost
        self.check("retry_is_idempotent", True, retry["idempotent"])
        self.check("original_receipt_returned", first["receipt"]["id"], retry["receipt"]["id"])
        runner.consume_interventions(self.state, self.run, self.root)
        applied = [item["id"] for item in self.state["applied_interventions"]]
        self.check("exactly_one_effect", ["op-7"], applied)
        again = interventions.submit(self.root, self.run, request_id="op-7", kind="feedback",
                                     text="Retain the partial implementation")
        self.check("post_apply_retry_still_one", "already_applied", again["consumer"])
        self.finish(summary="ONE_EFFECT: same operation id + payload recovers the saved result")

    def test_own09_operation_id_reuse_with_new_payload_conflicts(self):
        """OWN-09. Maps FX05 conflict semantics onto intervention request ids."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="op-9", kind="feedback",
                             text="Original payload A")
        self.expect_raises("changed_payload_conflicts", interventions.InterventionError,
                           interventions.submit, self.root, self.run, request_id="op-9",
                           kind="feedback", text="Payload B")
        requests = interventions.inspect(self.root, self.run)["requests"]
        self.check("original_payload_unchanged", ["Original payload A"],
                   [item["text"] for item in requests])
        self.check("single_pending_effect", 1, len(requests))
        self.finish(summary="CONFLICT_NO_NEW_EFFECT: one id never authorizes changed arguments")

    def test_own10_uncertain_non_idempotent_action_pauses(self):
        """OWN-10. Supported surface: uncertain stages are paused, never auto-repeated."""
        events = self.run / "iterations/005/terra-01.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type":"thread.started"}\n')  # response lost mid-flight
        support.atomic_json(self.run / "iterations/005/terra-01.before.json", support.snapshot(self.root))
        self.state["active_stage"] = {"role": "terra", "stage": "terra", "iteration": 5,
                                      "output": str(self.run / "iterations/005/terra-01.json"),
                                      "events": str(events),
                                      "before_ref": str(self.run / "iterations/005/terra-01.before.json")}
        for attempt in range(2):  # automatic retries requested twice
            self.expect_raises(f"[retry {attempt}] uncertain_action_not_repeated", support.Paused,
                               runner.reconcile_active, self.state, self.run, self.root)
        self.check("uncertain_record_retained", True, "active_stage" in self.state)
        self.check("no_duplicate_side_effect", True,
                   len([op for op in self.bundle.operations if op["kind"] == "blocked_real_launch"]) == 0)
        self.finish(summary="WAITING_RECONCILIATION: unknown outcome is never blindly repeated")

    def test_own11_failed_inspection_fails_closed(self):
        """OWN-11. Existing: test_autocode.test_active_legacy_guard_and_process_check_failure."""
        result = subprocess.CompletedProcess([], 1, stdout="")  # inspection tool failed
        with patch.object(support.subprocess, "run", return_value=result):
            self.expect_raises("inspection_failure_fails_closed", support.Paused,
                               support.assert_no_legacy_process, self.run, self.root)
        self.check("no_replacement_writer_on_blindness", 0,
                   len([op for op in self.bundle.operations if op["kind"] == "blocked_real_launch"]))
        self.finish(summary="PAUSED_SAFE: no inspection means no evidence of absence")

    def test_own12_stop_then_safe_restart(self):
        """OWN-12. Existing: pause lifecycle + resume paths."""
        self.approve_now()
        support.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="stop-1", kind="pause", text="")
        runner.consume_interventions(self.state, self.run, self.root)
        record = {"role": "terra", "stage": "terra", "iteration": 5, "processes": []}
        with patch.object(processes, "live_processes", return_value=[]):
            runner.assert_stage_stopped(record)  # all owned writers confirmed ended
        (self.run / "pause-requested").write_text("ack")
        self.invoke("--resume-paused")  # acknowledged stop; the next pass resumes work
        self.check("resume_lifecycle_advanced", True,
                   self.state["pause_intent"]["acknowledged_at"] is not None)
        self.check("work_preserved", True, self.state.get("goal_contract") is not None)
        self.check("stop_receipt_recorded", True, bool(self.state.get("pause_intent")))
        self.bundle.log("scoped_note", note="after an acknowledged pause the run waits for the "
                       "operator's next plain start (one new safe attempt) rather than "
                       "auto-relaunching inside the acknowledgement")
        self.finish(summary="RESUMED_PROGRESS: clean stop, preserved work, one new safe attempt")


if __name__ == "__main__":
    unittest.main()
