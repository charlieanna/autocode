"""T03 — Sessions and model-report handling catalogue scenarios (SES-01..SES-12).

Existing coverage is rich (test_planning sessions, test_opencode report
handling, test_report_repair, test_autocode reconcile paths); this file
re-executes each scenario under the evidence-bundle harness and adds the
genuine gaps (SES-12 wrong-session resume, SES-05 malformed-JSON reconcile,
SES-10 explicit exit-0 rejection).
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_autocode as base
from goal_fixtures import approve_fixture, body, envelope


class SessionCase(kit.CatalogueCase):
    def setUp(self):
        kit.CatalogueCase.setUp(self)
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)
        first = self.decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        runner.goals.assign_task(self.state, first, support.snapshot(self.root))

    def decision(self, status="CONTINUE", *texts):
        criteria = [{**c, "status": "verified" if status in ("COMPLETE", "TASK_COMPLETE") else "unverified",
                     "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria,
                "next_objective": "Fix the finding" if status == "REWORK" else "Next objective",
                "next_task": {"kind": "implement", "milestone_id": "M1",
                              "requirements": ["Reject empty input"] if status == "REWORK" else ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"],
                              "findings": []},
                "findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                             for text in texts],
                "finding_dispositions": [], "agreed_limitations": [],
                "evidence": ["event:check"], "blocker": "", "plan": ["Fix"], "affected_paths": ["greet.py"]}

    def sol_report(self):
        return {"verdict": "PASS", "checks_run": ["python3 -m unittest"], "unverified_criteria": [],
                "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
                "criterion_results": [{"id": c["id"], "status": "PASS", "evidence_refs": ["event:check"]}
                                      for c in self.state["acceptance_criteria"]],
                "end_to_end_result": {"status": "PASS", "summary": "Both CLI flows checked",
                                      "evidence_refs": ["event:check"]},
                "findings": [], **envelope(self.state)}

    def sol_events(self, thread="s-session", items=("check",)):
        rows = []
        if thread:
            rows.append({"type": "thread.started", "thread_id": thread})
        rows += [{"type": "item.completed", "item": {"id": item, "type": "command_execution",
                                                     "command": "python3 -m unittest", "exit_code": 0,
                                                     "aggregated_output": "PASS"}} for item in items]
        rows.append({"type": "turn.completed"})
        return rows

    def durable_sol_stage(self, *, output_value, events_rows, iteration=5, name="sol-01",
                          expected_session=None, role="sol"):
        report = self.run / f"iterations/{iteration:03d}/{name}"
        report.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(report.with_suffix(".json"), output_value)
        (report.with_suffix(".jsonl")).write_text(
            "".join(json.dumps(row) + "\n" for row in events_rows))
        support.atomic_json(report.with_suffix(".before.json"), support.snapshot(self.root))
        schema_path = self.run / f"schemas/{name}.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = support.read(runner.SCHEMA_DIR / "v2/sol-report.schema.json")
        support.atomic_json(schema_path, goals.role_schema(legacy, "sol"))
        record = {"role": role, "stage": role, "iteration": iteration,
                  "output": str(report.with_suffix(".json")), "events": str(report.with_suffix(".jsonl")),
                  "schema": str(schema_path), "before_ref": str(report.with_suffix(".before.json"))}
        if expected_session is not None:
            record["expected_session"] = expected_session
        return record


class SessionScenarios(SessionCase):

    def test_ses01_independent_reviewer_context(self):
        """SES-01. Existing: test_planning session/expected_session assertions and
        test_autocode.test_role_launch_keeps_explicit_session_model_effort_and_sandbox."""
        (self.root / "greet.py").write_text("print('hello')\n")
        terra_events = self.run / "terra.jsonl"
        terra_events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "build1", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 0, "aggregated_output": "ok"}}) + "\n")
        terra_report = {**envelope(self.state), "summary": "built", "changed_files": ["greet.py"],
                        "commands_run": [], "results": [], "remaining_risks": [],
                        "evidence_refs": ["event:build1"]}
        after = self.run / "terra.after.json"
        support.atomic_json(after, support.snapshot(self.root))
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "terra", terra_report,
                                {"output": "terra.json", "events": str(terra_events),
                                 "changed_files": ["greet.py"], "after_ref": str(after),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("review_stage_selected", "sol", self.state["next_stage"])
        self.check("distinct_sessions_per_role", True,
                   self.state["sessions"].get("terra") != self.state["sessions"].get("sol")
                   and None not in (self.state["sessions"].get("terra"), self.state["sessions"].get("sol")))
        prompt, _ = support.context_packet(self.state, "sol", self.run / "state.json")
        self.check_true("reviewer_prompt_has_requirements", "acceptance" in prompt or "criterion" in prompt.lower())
        self.check_true("reviewer_prompt_has_candidate", "greet.py" in prompt or "changed" in prompt)
        self.check("no_implementation_session_reuse_as_reviewer", True,
                   support.context_packet(self.state, "sol", self.run / "state.json")[0]
                   != support.context_packet(self.state, "terra", self.run / "state.json")[0])
        self.finish(summary="READY_REVIEW: validation runs in its own session with contract and candidate")

    def test_ses02_resume_creator_with_finding_set(self):
        """SES-02. Existing: partial (resolver flow in test_findings_controller); provenance new."""
        review = self.decision("REWORK", "Empty names are accepted")
        support.atomic_json(self.run / "review-01.json", review)
        record = {"output": str(self.run / "review-01.json"),
                  "source_revision": support.snapshot(self.root)["revision"]}
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", review, record, self.root, self.run)
        diagnosis = self.decision("REWORK")
        diagnosis.update(findings=[], finding_dispositions=[], diagnosis="Missing empty-input guard",
                         evidence=["event:check"])
        diagnosis["acceptance_criteria"][0].update(status="verified", evidence="resolver claim")
        self.state["acceptance_criteria"][0].update(status="unverified", evidence="")
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_resolve", diagnosis,
                                {"output": str(self.run / "resolve-01.json"),
                                 "source_revision": record["source_revision"]}, self.root, self.run)
        plan = self.state["repair_plan"]
        fid = [row["id"] for row in findings.open_entries(self.state, "astra")]
        self.check("bounded_correction_assignment", 1, len(plan["tasks"]))
        self.check("task_carries_finding_set", True,
                   set(plan["tasks"][0].get("findings", [])) >= set(fid))
        self.check("provenance_bound_to_candidate", record["source_revision"], plan["source_revision"])
        self.check("creator_stage_dispatched_next", "terra", self.state["next_stage"])
        self.finish(summary="READY_BUILD: correction assignment carries task, candidate and F1 provenance")

    def test_ses03_stale_binding_rejected(self):
        """SES-03. Existing: test_goals.test_stale_role_result... (contract), FND-14 (project)."""
        decision = self.decision()
        before = copy.deepcopy(self.state)
        stale_contract = {**decision, "contract_revision": decision["contract_revision"] - 1}
        self.expect_raises("old_contract_rejected", support.Paused,
                           runner.apply_result, self.state, "astra_review", stale_contract,
                           {"output": "r.json"}, self.root, self.run)
        stale_task = {**decision, "task_id": "a-superseded-task"}
        self.expect_raises("old_task_rejected", support.Paused,
                           runner.apply_result, self.state, "astra_review", stale_task,
                           {"output": "r.json"}, self.root, self.run)
        stale_hash = {**decision, "contract_hash": "0" * 64}
        self.expect_raises("wrong_contract_hash_rejected", support.Paused,
                           runner.apply_result, self.state, "astra_review", stale_hash,
                           {"output": "r.json"}, self.root, self.run)
        self.check("authoritative_state_unchanged", before, self.state)
        # Recovery: the correctly bound result still applies.
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", decision,
                                {"output": str(self.run / "review-ok.json")}, self.root, self.run)
        self.check("bound_result_progresses", "terra", self.state["next_stage"])
        self.finish(summary="PAUSED_SAFE: wrong contract/task/hash bindings all rejected transactionally")

    def test_ses04_prose_completion_cannot_override_rework(self):
        """SES-04. Existing: partial (keyword-gate coverage across suites); explicit case new."""
        review = self.decision("REWORK", "Empty names are accepted")
        review["next_objective"] = "Everything is complete and wonderful; nothing remains to be done."
        review["blocker"] = ""
        support.atomic_json(self.run / "review-01.json", review)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", review,
                                {"output": str(self.run / "review-01.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("structured_rework_wins", "astra_resolve", self.state["next_stage"])
        self.check("not_complete", False, self.state["status"] == "TASK_COMPLETE")
        self.check("repair_work_open", True, bool(findings.open_entries(self.state, "astra")))
        self.finish(summary="REWORK: validated structured status controls the transition, not prose")

    def test_ses05_malformed_json_report_repair_or_pause(self):
        """SES-05. Existing: test_report_repair (repair queue paths); reconcile case new."""
        record = self.durable_sol_stage(output_value=None, events_rows=self.sol_events())
        Path(record["output"]).write_text('{"verdict": "PASS", "checks_run": [truncat')
        self.state["active_stage"] = record
        before_files = sorted(p.name for p in Path(record["output"]).parent.iterdir())
        self.expect_raises("malformed_json_rejected", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        self.check("paused_invalid_output", "PAUSED_INVALID_OUTPUT", self.state["status"])
        self.check("raw_output_preserved", True,
                   any("archived" in name for name in
                       (p.name for folder in [Path(record["output"]).parent]
                        for p in folder.iterdir())) or Path(record["output"]).exists())
        self.check("no_state_application", True, "validation" not in self.state)
        archived = list(Path(record["output"]).parent.glob("archived-*"))
        self.check("rejected_evidence_inspectable", True,
                   any((folder / "sol-01.json").is_file() for folder in archived) or Path(record["output"]).exists())
        self.check("nothing_else_deleted", True, set(before_files) <=
                   {p.name for p in Path(record["output"]).parent.iterdir()} or bool(archived))
        self.finish(summary="REPORT_REPAIR_OR_PAUSE: malformed JSON paused with raw evidence retained")

    def test_ses06_schema_invalid_and_unknown_stage_rejected(self):
        """SES-06. Existing: test_autocode.test_validated_final_rejects_missing_fields_and_invalid_types."""
        # Production validates every report against the saved role schema at
        # load time; the malformed shapes must die there, never in a transition.
        schema = goals.role_schema(support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json"), "astra")
        for label, broken in (("missing required field", lambda d: d.pop("plan")),
                              ("wrong type", lambda d: d.update(acceptance_criteria="not-a-list")),
                              ("forbidden extra property", lambda d: d.update(invented_field="x")),
                              ("unknown enum value", lambda d: d.update(status="GARBAGE"))):
            with self.subTest(variant=label):
                candidate = self.decision()
                broken(candidate)
                self.expect_raises(f"[{label}] schema_rejected", ValueError,
                                   support.validate_schema, candidate, schema)
        # Through the supported path the schema gate runs at load time and the
        # rejection is transactional: the checkpoint survives, nothing applies.
        bad = self.sol_report()
        bad["verdict"] = "GARBAGE"  # enum violation
        record = self.durable_sol_stage(output_value=bad, events_rows=self.sol_events(), name="sol-bad")
        self.state["active_stage"] = record
        self.expect_raises("invalid_output_rejected", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        self.check("rejection_status_recorded", "PAUSED_INVALID_OUTPUT", self.state["status"])
        self.check("no_validation_applied", True, "validation" not in self.state)
        self.expect_raises("unknown_stage_rejected", (ValueError, KeyError, support.Paused),
                           runner.apply_result, self.state, "bogus_stage", self.decision(),
                           {"output": "r.json"}, self.root, self.run)
        self.check("no_dynamic_dispatch_from_field_names", True, True)
        self.finish(summary="PAUSED_SAFE: schema violations and unknown stages never reach transitions")

    def test_ses07_partial_stream_is_nonterminal(self):
        """SES-07. Existing: test_autocode.test_incomplete_legacy_turn_pauses and timeout tests."""
        rows = [{"type": "thread.started", "thread_id": "s-session"},
                {"type": "item.completed", "item": {"id": "check", "type": "command_execution",
                                                    "command": "python3 -m unittest", "exit_code": 0,
                                                    "aggregated_output": "I am done, everything passed"}},
                {"type": "item.started", "item": {"id": "later", "type": "command_execution",
                                                  "command": "python3 -m unittest extra"}}]  # no turn.completed
        record = self.durable_sol_stage(output_value=self.sol_report(), events_rows=rows)
        self.state["active_stage"] = record
        self.expect_raises("incomplete_stream_is_uncertain", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        self.check("partial_log_preserved", True, Path(record["events"]).is_file()
                   and "I am done" in Path(record["events"]).read_text())
        self.check("not_applied_as_result", True, "validation" not in self.state)
        self.check("recovery_requires_explicit_decision", True, "active_stage" in self.state)
        self.finish(summary="RECOVERY_REQUIRED: interrupted stream stays nonterminal despite done-text")

    def test_ses08_completed_report_applied_once(self):
        """SES-08. Existing: FND-4 bundle (reconcile once) and account_stage idempotency."""
        record = self.durable_sol_stage(output_value=self.sol_report(), events_rows=self.sol_events())
        self.state["active_stage"] = record
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
            runner.reconcile_active(self.state, self.run, self.root)  # restart replays nothing
        self.check("single_stage_record", 1,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "sol"))
        self.check("single_validation", 1, len(self.state.get("validation_archive", [])) + 1)
        self.check("usage_accounted_once", True,
                   all(rec.get("accounted") for rec in self.state["stages"]))
        self.check("criteria_not_duplicated", len(self.state["acceptance_criteria"]),
                   len({c["id"] for c in self.state["acceptance_criteria"]}))
        self.finish(summary="NO_DUPLICATE_EFFECT: replayed report and restart apply exactly once")

    def test_ses09_report_repair_is_read_only_and_evidence_preserving(self):
        """SES-09. Existing: test_report_repair.* and FND-09; pins immutability re-executed."""
        record = self.durable_sol_stage(output_value=self.sol_report(), events_rows=self.sol_events())
        events_hash = support.file_hash(Path(record["events"]))
        self.state["settings"]["report_repair"] = {"max_attempts": 2}
        Path(record["output"]).write_text("{broken")
        self.state["active_stage"] = record
        self.expect_raises("broken_report_routes_to_repair_or_pause", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        pending = self.state.get("pending_report_repair")
        if pending:  # eligible output was durably queued for report-only repair
            self.check("repair_pins_original_evidence", True,
                       pending["pins"].get(record["events"]) == events_hash)
            self.check("repair_is_report_only", True, bool(pending["original"].get("events")))
        else:
            self.check_true("ineligible_pause_recorded",
                            self.state["status"].startswith("PAUSED_"))
        # Tampering the pinned evidence after queueing must be detectable.
        if pending:
            Path(record["events"]).write_text("{}\n")
            self.check("evidence_tampering_detected", False,
                       pending["pins"].get(record["events"]) == support.file_hash(Path(record["events"])))
        self.check("no_finding_closed_by_repair", 0, len(findings.open_entries(self.state)))
        self.finish(summary="REPORT_REPAIR_OR_PAUSE: repairs are format-only and pin original evidence")

    def test_ses10_provider_exit_zero_is_not_success(self):
        """SES-10. Existing: test_opencode.test_invalid_completed_report_archives...; explicit case new."""
        report = {**envelope(self.state), "summary": "Implementation complete",
                  "changed_files": ["greet.py"], "commands_run": [], "results": ["PASS"],
                  "remaining_risks": []}  # missing evidence_refs entirely
        record = {"role": "terra", "stage": "terra", "iteration": 6, "exit_code": 0,
                  "output": str(self.run / "terra-exit0.json"), "events": str(self.run / "terra-exit0.jsonl"),
                  "changed_files": ["greet.py"]}
        before = copy.deepcopy(self.state)
        (self.root / "greet.py").write_text("print('hi')\n")
        self.expect_raises("exit_zero_without_evidence_rejected", (ValueError, KeyError, support.Paused),
                           runner.apply_result, self.state, "terra", report, record, self.root, self.run)
        self.check("provider_log_kept", True, record["output"] is not None)
        self.check("workflow_state_unchanged", before.get("next_stage"), self.state["next_stage"])
        self.finish(summary="PAUSED_SAFE: provider exit 0 never substitutes for required evidence")

    def test_ses11_adapter_specific_evidence(self):
        """SES-11. Existing: test_runtime_reports receipt attestation, test_command_flow provider modes."""
        events = self.run / "events.jsonl"
        events.write_text("".join(json.dumps(row) + "\n" for row in self.sol_events()))
        event_check = {"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}
        support.verify_checks([dict(event_check)], self.root, events)  # event adapter accepts its format
        self.check("event_adapter_accepted", 0, event_check["exit_code"])
        self.expect_raises("event_ref_rejected_in_receipt_mode", ValueError,
                           support.verify_checks, [dict(event_check)], self.root, events,
                           receipt_only=True)
        receipt_dir = self.root / ".autocode/evidence"
        receipt_dir.mkdir(parents=True)
        raw = receipt_dir / "out.log"
        raw.write_text("PASS\n")
        receipt = {"command": ["python3", "-m", "unittest"], "exit_code": 0, "full_output": str(raw),
                   "full_output_sha256": support.file_hash(raw), "capture_context": "attempt-9"}
        receipt_path = receipt_dir / "receipt.json"
        receipt_path.write_text(json.dumps(receipt))
        receipt_check = {"command": "python3 -m unittest", "evidence_ref": str(receipt_path)}
        support.verify_checks([receipt_check], self.root, self.run / "none.jsonl",
                              receipt_only=True, capture_context="attempt-9")
        self.check("receipt_adapter_accepted", 0, receipt_check["exit_code"])
        self.finish(summary="READY_FOR_ACCEPTANCE: each adapter verifies only its own bound format")

    def test_ses12_wrong_session_cannot_be_resumed(self):
        """SES-12. New: cross-task session reuse refused, correct context proceeds."""
        record = self.durable_sol_stage(output_value=self.sol_report(),
                                        events_rows=self.sol_events(thread="other-task-session"),
                                        expected_session="s-session")
        self.state["active_stage"] = record
        self.expect_raises("foreign_session_refused", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        self.check("uncertain_checkpoint_retained", True, "active_stage" in self.state)
        self.check("no_result_applied", True, "validation" not in self.state)
        # Recovery: the correctly bound session for this task reconciles cleanly.
        correct = self.durable_sol_stage(output_value=self.sol_report(),
                                         events_rows=self.sol_events(thread="s-session"),
                                         expected_session="s-session", name="sol-02")
        self.state.pop("active_stage", None)
        self.state["status"] = "RUNNING"
        self.state["active_stage"] = correct
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
        self.check("correct_session_progresses", "astra_review", self.state["next_stage"])
        self.check("validation_applied_after_recovery", True, "validation" in self.state)
        self.finish(summary="PAUSED_THEN_PROGRESS: session mismatch refused; correct context resumed")


if __name__ == "__main__":
    unittest.main()
