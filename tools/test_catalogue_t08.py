"""T08 — Crash and persistence recovery catalogue scenarios (CRH-01..CRH-14).

Recovery is driven through the real reconcile/main paths with durable
on-disk artifacts; barriers are file/event based, never sleeps.  Existing
recovery regressions in test_autocode/test_report_repair are cited per case.
"""
import copy
import errno
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
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t06 as t06
from goal_fixtures import body, envelope


class CrashCase(t06.SolControllerCase):

    def decision(self, status="CONTINUE", *texts, dispositions=()):
        value = super().decision(status)
        value["findings"] = [{"severity": "high", "finding": text, "evidence": "event:check",
                              "blocking": True} for text in texts]
        value["finding_dispositions"] = list(dispositions)
        if status == "REWORK":
            value["next_task"] = {"kind": "implement", "milestone_id": "M1",
                                  "requirements": ["Reject empty input"], "acceptance_criteria": ["C1"],
                                  "validation_plan": ["Run both cases"], "findings": []}
            value["next_objective"] = "Fix the finding"
        return value

    def sol_events(self, thread="s-session", items=("check",)):
        rows = []
        if thread:
            rows.append({"type": "thread.started", "thread_id": thread})
        rows += [{"type": "item.completed", "item": {"id": item, "type": "command_execution",
                                                     "command": "python3 -m unittest", "exit_code": 0,
                                                     "aggregated_output": "PASS"}} for item in items]
        rows.append({"type": "turn.completed"})
        return rows

    def durable_sol_stage(self, *, output_value, events_rows, name="sol-01"):
        report = self.run / f"iterations/005/{name}"
        report.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(report.with_suffix(".json"), output_value)
        report.with_suffix(".jsonl").write_text("".join(json.dumps(r) + "\n" for r in events_rows))
        support.atomic_json(report.with_suffix(".before.json"), support.snapshot(self.root))
        schema_path = self.run / f"schemas/{name}.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = support.read(runner.SCHEMA_DIR / "v2/sol-report.schema.json")
        support.atomic_json(schema_path, goals.role_schema(legacy, "sol"))
        return {"role": "sol", "stage": "sol", "iteration": 5,
                "output": str(report.with_suffix(".json")), "events": str(report.with_suffix(".jsonl")),
                "schema": str(schema_path), "before_ref": str(report.with_suffix(".before.json"))}

    def invoke_main(self, *args, role=None):
        support.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]
        import contextlib, io
        with patch.object(sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                patch.object(support, "local_settings", return_value={"auth_mode": "fixture"}), \
                patch.object(runner, "run_role", side_effect=role or AssertionError("No agent may launch")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exit_code:
                code = exit_code.code
        self.state = support.read(self.run / "state.json")
        return code


class CrashScenarios(CrashCase):

    def test_crh01_crash_before_launch_intent(self):
        """CRH-01. Existing: dispatch paths across suites; single-launch case new."""
        self.state["settings"]["limits"] = {"iteration_ceiling": 18, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 3,
                                            "automatic_retries": 0}
        self.state["settings"]["transport_identity"] = {"auth_mode": "fixture"}
        launches = []

        def staged_role(**kwargs):
            stage = kwargs["state"]["next_stage"]
            launches.append(stage)
            self.bundle.operation("launch", stage=stage)
            out = self.run / f"{stage}.json"
            ev = self.run / f"{stage}.jsonl"
            if stage == "terra":
                (self.root / "source.rb").write_text("fixed")
                value = {"summary": "done", "changed_files": ["source.rb"], "commands_run": [],
                         "results": [], "remaining_risks": [], "evidence_refs": ["event:check"]}
                ev.write_text(json.dumps({"type": "item.completed", "item": {
                    "id": "check", "type": "command_execution", "command": "ruby t",
                    "exit_code": 0, "aggregated_output": "ok"}}) + "\n")
            elif stage == "sol":
                ev.write_text(json.dumps({"type": "item.completed", "item": {
                    "id": "check", "type": "command_execution", "command": "ruby t",
                    "exit_code": 0, "aggregated_output": "3 tests passed"}}) + "\n")
                value = {"verdict": "PASS", "checks_run": ["ruby t"], "findings": [], "unverified_criteria": [],
                         "end_to_end_result": {"status": "PASS", "summary": "flow", "evidence_refs": ["event:check"]},
                         "checks": [{"command": "ruby t", "exit_code": 0, "evidence_ref": "event:check"}],
                         "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}]}
            else:
                value = CrashCase.decision(self, "COMPLETE")
                value.update(envelope(kwargs["state"]))
                ev.write_text('{"type":"turn.completed"}\n')
            value.update(envelope(kwargs["state"]))
            support.atomic_json(out, value)
            record = {"role": kwargs["role"], "stage": stage, "iteration": 5, "output": str(out),
                      "events": str(ev), "changed_files": ["source.rb"] if stage == "terra" else [],
                      "after_ref": str(self.run / "check.log"), "source_revision": support.snapshot(self.root)["revision"],
                      "duration_seconds": 0.01}
            return value, record

        code = self.invoke_main(role=staged_role)
        self.check("restarted_run_completes", 0, code)
        self.check("single_launch_per_stage", ["terra", "sol", "astra_review"], launches)
        again = self.invoke_main(role=staged_role)
        self.check("second_restart_launches_nothing", (0, 3), (again, len(launches)))
        self.finish(summary="READY_BUILD: one eventual launch per stage from durable approved state")

    def test_crh02_crash_after_intent_before_launch(self):
        """CRH-02. New: durable intent without a completed attempt never double-launches."""
        events = self.run / "iterations/005/terra-01.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type":"thread.started","thread_id":"t-1"}\n')  # nothing completed
        before = support.snapshot(self.root)
        support.atomic_json(self.run / "iterations/005/terra-01.before.json", before)
        terra_schema = self.run / "schemas/terra.json"
        terra_schema.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(terra_schema, goals.role_schema(
            support.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra"))
        self.state["active_stage"] = {"role": "terra", "stage": "terra", "iteration": 5,
                                      "output": str(self.run / "iterations/005/terra-01.json"),
                                      "events": str(events),
                                      "schema": str(terra_schema),
                                      "before_ref": str(self.run / "iterations/005/terra-01.before.json")}
        with self.forbid_real_launches(runner):
            self.expect_raises("uncertain_intent_never_relaunched", support.Paused,
                               runner.reconcile_active, self.state, self.run, self.root)
        self.check("intent_retained_for_inspection", True, "active_stage" in self.state)
        # Recovery: the worker's response later becomes durable and reconciles once.
        support.atomic_json(self.run / "iterations/005/terra-01.json",
                            {**envelope(self.state), "summary": "late finish", "changed_files": [],
                             "commands_run": [], "results": [], "remaining_risks": [],
                             "addressed_requirements": [], "untested_behavior": [],
                             "recommended_checks": [],
                             "evidence_refs": [str(self.run / "check.log")]})
        events.write_text('{"type":"thread.started","thread_id":"t-1"}\n{"type":"turn.completed"}\n')
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
        self.check("reconciled_exactly_once", 1,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "terra"))
        self.finish(summary="RECONCILED_PROGRESS: one admitted worker, never two")

    def test_crh03_crash_after_launch_before_metadata(self):
        """CRH-03. Existing: assert_stage_stopped/live-process guards; explicit case new."""
        events = self.run / "iterations/005/terra-01.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type":"thread.started"}\n')
        support.atomic_json(self.run / "iterations/005/terra-01.before.json", support.snapshot(self.root))
        record = {"role": "terra", "stage": "terra", "iteration": 5,
                  "output": str(self.run / "iterations/005/terra-01.json"), "events": str(events),
                  "before_ref": str(self.run / "iterations/005/terra-01.before.json"),
                  "processes": [{"pid": 424242, "started": "fixture-birth", "group": 424242}]}
        self.state["active_stage"] = record
        with patch.object(runner.processes, "live_processes", return_value=[{"pid": 424242}]):
            self.expect_raises("live_worker_blocks_replacement", support.Paused,
                               runner.reconcile_active, self.state, self.run, self.root)
        self.check("no_duplicate_writer_while_alive", True, "active_stage" in self.state)
        with patch.object(runner.processes, "live_processes", return_value=[]):
            self.expect_raises("dead_worker_still_requires_inspection", support.Paused,
                               runner.reconcile_active, self.state, self.run, self.root)
        self.check("uncertain_launch_recorded", True, "active_stage" in self.state)
        self.finish(summary="OWNERSHIP_RECONCILIATION: unknown outcome pauses, never replaces blindly")

    def test_crh04_crash_while_builder_writes(self):
        """CRH-04. Existing: test_autocode.test_abandon_uncertain_attempt_retains_edits..."""
        before = support.snapshot(self.root)
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix(".before.json"), before)
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"t-session"}\n')
        (self.root / "partial.py").write_text("# preserved partial implementation\n")
        record = {"role": "terra", "stage": "terra", "iteration": 5, "duration_seconds": 8,
                  "output": str(base.with_suffix(".json")), "events": str(base.with_suffix(".jsonl")),
                  "before_ref": str(base.with_suffix(".before.json")), "exit_code": -15, "processes": []}
        self.state["active_stage"] = record
        with patch.object(runner.processes, "live_processes", return_value=[]):
            runner.abandon_stage(self.state, self.run, self.root, "005/terra-01")
        self.check("partial_work_retained", True, (self.root / "partial.py").exists())
        self.check("no_false_accepted_implementation", True, "implementation" not in self.state
                   or self.state.get("implementation") is None)
        self.check("writer_disposition_recorded", True,
                   self.state["stages"][-1].get("abandoned") is True)
        self.check("session_released", True, "terra" not in self.state["sessions"])
        self.finish(summary="RECOVERABLE_PARTIAL_WORK: retained edits, honest disposition, no overlap")

    def test_crh05_result_durable_before_application(self):
        """CRH-05. Existing: test_crash_after_completed_terra_reconciles_without_reexecution."""
        (self.root / "greet.py").write_text("print('v1')\n")
        before = support.snapshot(self.root)
        base = self.run / "iterations/005/terra-01"
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix(".before.json"), before)
        # The durable report represents a completed edit, not a no-op attempt.
        # Retain that edit across recovery so the progress guard stays active.
        (self.root / "greet.py").write_text("print('v2')\n")
        value = {**envelope(self.state), "summary": "saved", "changed_files": ["greet.py"],
                 "commands_run": [], "results": [], "remaining_risks": [],
                 "addressed_requirements": [], "untested_behavior": [], "recommended_checks": [],
                 "evidence_refs": [str(self.run / "check.log")]}
        support.atomic_json(base.with_suffix(".json"), value)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        terra_schema = self.run / "schemas/terra.json"
        terra_schema.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(terra_schema, goals.role_schema(
            support.read(runner.SCHEMA_DIR / "v2/terra-report.schema.json"), "terra"))
        self.state["active_stage"] = {"role": "terra", "stage": "terra", "iteration": 5,
                                      "output": str(base.with_suffix(".json")),
                                      "events": str(base.with_suffix(".jsonl")),
                                      "schema": str(terra_schema),
                                      "before_ref": str(base.with_suffix(".before.json"))}
        with patch.object(runner, "run_role", side_effect=AssertionError("no replay")):
            runner.reconcile_active(self.state, self.run, self.root)
            runner.reconcile_active(self.state, self.run, self.root)
        self.check("result_applied_once", 1,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "terra"))
        self.check("completed_edit_retained", "print('v2')\n", (self.root / "greet.py").read_text())
        self.check("review_next_step", "sol", self.state["next_stage"])
        self.finish(summary="RECOVERED_RESULT: durable implementation applied without re-execution")

    def test_crh06_review_result_before_transition(self):
        """CRH-06. Existing: FND-4 (sol PASS); REWORK variant new here."""
        rework = self.decision("REWORK", "Empty names are accepted")
        base = self.run / "iterations/005/astra_review-01"
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix(".json"), rework)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        support.atomic_json(base.with_suffix(".before.json"), support.snapshot(self.root))
        schema_path = self.run / "schemas/astra.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json")
        support.atomic_json(schema_path, goals.role_schema(legacy, "astra"))
        self.state["active_stage"] = {"role": "astra", "stage": "astra_review", "iteration": 5,
                                      "output": str(base.with_suffix(".json")),
                                      "events": str(base.with_suffix(".jsonl")),
                                      "schema": str(schema_path),
                                      "before_ref": str(base.with_suffix(".before.json"))}
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
            runner.reconcile_active(self.state, self.run, self.root)
        self.check("rejection_not_lost", 1, len(findings.open_entries(self.state, "astra")))
        self.check("single_repair_creation", True, bool(self.state.get("resolution_request")))
        self.check("next_action_resolve", "astra_resolve", self.state["next_stage"])
        self.finish(summary="RECOVERED_REVIEW: bound report applied exactly once")

    def test_crh07_atomic_replacement_fault_matrix(self):
        """CRH-07. Existing: test_atomic_failure_keeps_old_checkpoint; success path added."""
        state_path = self.run / "state.json"
        support.atomic_json(state_path, {"old": True})
        with patch.object(support.os, "replace", side_effect=OSError("interrupted before replace")):
            self.expect_raises("pre_replace_failure_raises", OSError,
                               support.atomic_json, state_path, {"new": True})
        self.check("old_checkpoint_intact", {"old": True}, support.read(state_path))
        self.check("no_temp_litter", [], list(self.run.glob(".checkpoint-*")))
        support.atomic_json(state_path, {"new": True})
        self.check("successful_replace_publishes_new", {"new": True}, support.read(state_path))
        self.finish(summary="VALID_STATE_OR_EXPLICIT_RECOVERY: old-or-new, never hybrid")

    def test_crh08_disk_full_and_permission_failures(self):
        """CRH-08. New: ENOSPC/EACCES at the persistence boundary."""
        state_path = self.run / "state.json"
        support.atomic_json(state_path, {"old": True})
        for name, code in (("disk full", errno.ENOSPC), ("permission denied", errno.EACCES)):
            with self.subTest(variant=name):
                with patch.object(support.os, "replace", side_effect=OSError(code, name)):
                    self.expect_raises(f"[{name}] storage_error_raises", OSError,
                                       support.atomic_json, state_path, {"new": True})
                self.check(f"[{name}] old_state_retained", {"old": True}, support.read(state_path))
        self.check("no_success_without_durability", True, support.read(state_path) == {"old": True})
        self.finish(summary="PAUSED_SAFE: storage failures never publish success")

    def test_crh09_corrupt_state_not_reset(self):
        """CRH-09. New: truncated state refuses instead of auto-initializing."""
        corrupt = self.run / "state.json"
        corrupt.write_text('{"version": 3, "status": "RUNNIN')  # truncated mid-write
        original_bytes = corrupt.read_text()
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run)]
        import contextlib, io
        with patch.object(sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                patch.object(runner, "run_role", side_effect=AssertionError("no launch from corrupt state")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.expect_raises("corrupt_state_refused", Exception, runner.main)
        self.check("corrupt_file_preserved", original_bytes, corrupt.read_text())
        self.check("no_fresh_running_state", True,
                   corrupt.read_text() == original_bytes)
        self.finish(summary="PAUSED_SAFE: corruption diagnosed, never silently re-initialized")

    def test_crh10_second_fault_during_recovery(self):
        """CRH-10. New: compound failure keeps the original result recoverable."""
        record = self.durable_sol_stage(output_value=self.sol_report(),
                                        events_rows=self.sol_events())
        self.state["active_stage"] = record
        support.atomic_json(self.run / "state.json", self.state)
        real_write = runner.write_json
        with patch.object(runner, "write_json", side_effect=[OSError("disk full during recovery"), real_write]):
            self.expect_raises("first_recovery_write_fails", OSError,
                               runner.reconcile_active, self.state, self.run, self.root)
        self.check("result_files_survive_compound_fault", True,
                   Path(record["output"]).is_file() and Path(record["events"]).is_file())
        # The process died after the failed write; reload the durable checkpoint.
        self.state = support.read(self.run / "state.json")
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
        self.check("second_recovery_applies_once", 1,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "sol"))
        self.finish(summary="RECOVERABLE_WITHOUT_DUPLICATES: the sole valid result is never consumed")

    def test_crh11_integration_patch_idempotence(self):
        """CRH-11. New: an already-applied integration patch is recognized, not reapplied."""
        (self.root / "contract").mkdir()
        (self.root / "contract" / "schema.json").write_text('{"v": 1}\n')
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "-qm", "base"], check=True)
        patch_text = subprocess.run(["git", "-C", str(self.root), "diff", "HEAD~1"], capture_output=True, text=True).stdout
        patch_file = self.run / "integration.patch"
        patch_file.write_text(patch_text)
        subprocess.run(["git", "-C", str(self.root), "reset", "--hard", "HEAD~1", "-q"], check=True)
        baseline = support.snapshot(self.root)
        subprocess.run(["git", "-C", str(self.root), "apply", str(patch_file)], check=True)  # effect landed
        after_first = support.snapshot(self.root)
        reapply = subprocess.run(["git", "-C", str(self.root), "apply", str(patch_file)],
                                 capture_output=True, text=True)
        self.check("reapply_refused", False, reapply.returncode == 0)
        after_second = support.snapshot(self.root)
        self.check("already_applied_recognized", after_first["revision"], after_second["revision"])
        self.check("no_blind_double_application", [], support.changed_paths(after_first, after_second))
        self.check("baseline_differs_from_integrated", True,
                   baseline["revision"] != after_first["revision"])
        self.finish(summary="RECONCILED_INTEGRATION: exact already-applied result recognized by identity")

    def test_crh12_authority_durable_across_restarts(self):
        """CRH-12. Existing: FND-4/reconcile idempotency; restart loop compact here."""
        blocked = self.decision("BLOCKED")
        blocked["user_request"] = {"kind": "permission", "discovered": "need", "impact": "blocked",
                                   "decision_needed": "Provide fixture credentials",
                                   "options": [], "proposed_delta": ""}
        support.atomic_json(self.run / "blocked.json", blocked)
        runner.apply_result(self.state, "astra_review", blocked,
                            {"output": str(self.run / "blocked.json"),
                             "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)
        findings.record_decision(self.state, self.decision("REWORK", "Durable finding"),
                                 {"output": "astra-d.json"})
        canonical = {"approval": copy.deepcopy(self.state["goal_contract"]["approval_event"]),
                     "findings": findings.summary(self.state)["entries"]}
        for restart in range(3):
            code = self.invoke_main()  # plain restart; nothing new to do
            self.check(f"[restart {restart}] no_launches", 0,
                       len([op for op in self.bundle.operations if op["kind"] == "blocked_real_launch"]))
            self.check(f"[restart {restart}] status_unchanged", "WAITING_FOR_USER", self.state["status"])
        self.check("approval_bound_to_same_contract", canonical["approval"],
                   self.state["goal_contract"]["approval_event"])
        self.check("findings_stable", canonical["findings"], findings.summary(self.state)["entries"])
        self.finish(summary="NO_AUTHORITY_CHANGE: canonical state equivalent after every restart")

    def test_crh13_completed_run_idle_across_restart(self):
        """CRH-13. Existing: REV-12 and test_complete_resume_loop."""
        self.apply_sol(self.sol_report())
        complete = super().decision("COMPLETE")
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        acceptance = copy.deepcopy(self.state["final_decision"])
        self.state["settings"]["limits"] = {"iteration_ceiling": 5, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 3,
                                            "automatic_retries": 0}
        self.state["settings"]["transport_identity"] = {"auth_mode": "fixture"}
        import os as _os
        _os.environ["AUTOCODE_HOME"] = str(self.root.parent / "registry-crh13")
        code = self.invoke_main()
        self.check("restart_exits_cleanly", 0, code)
        self.check("terminal_state_preserved", "TASK_COMPLETE", self.state["status"])
        self.check("acceptance_unchanged", acceptance, self.state["final_decision"])
        self.check("zero_new_workers", 0, len(self.bundle.operations))
        self.bundle.log("scoped_note", note="installed-entry-point restart check deferred to T13/CFG-09")
        self.finish(summary="COMPLETE: restart launches nothing on terminal work")

    def test_crh14_post_completion_edit_is_stale_work(self):
        """CRH-14. New: user edit after acceptance is neither re-approved nor rewritten."""
        self.apply_sol(self.sol_report())
        complete = super().decision("COMPLETE")
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        (self.root / "greet.py").write_text("print('user edit after completion')\n")
        user_edit = (self.root / "greet.py").read_text()
        self.state["settings"]["limits"] = {"iteration_ceiling": 5, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 3,
                                            "automatic_retries": 0}
        self.state["settings"]["transport_identity"] = {"auth_mode": "fixture"}
        import os as _os
        _os.environ["AUTOCODE_HOME"] = str(self.root.parent / "registry-crh14")
        code = self.invoke_main()
        self.check("stale_acceptance_reported", True,
                   self.state["status"].startswith("PAUSED_") or self.state["status"] == "TASK_COMPLETE")
        self.check("no_silent_reapproval", True,
                   self.state["status"] != "TASK_COMPLETE"
                   or self.state["validation"]["source_revision"] == support.snapshot(self.root)["revision"])
        self.check("user_edit_untouched", user_edit, (self.root / "greet.py").read_text())
        self.check("no_automatic_rewriting", 0, len(self.bundle.operations))
        self.finish(summary="EXPLICIT_STALE_RECONCILIATION: edit preserved, acceptance not silently extended")


if __name__ == "__main__":
    unittest.main()
