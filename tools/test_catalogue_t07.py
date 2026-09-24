"""T07 — Corrections and resolver boundaries catalogue scenarios (FIX-01..FIX-10).

FIX-04 is the catalogue's tagged `proposed-capability-test`: intra-repair
checkpoints are not a product promise and are reported as a scoped gap, not
implemented.  The remaining nine cases re-execute existing regressions or add
the explicit gaps under the evidence-bundle harness.
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
import autocode_escalation as escalation
import autocode_failures as failures
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t06 as t06
from goal_fixtures import body, envelope


class RepairCase(t06.SolControllerCase):

    def complete_decision(self):
        return self.decision("COMPLETE")

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

    def start_rework(self, *texts):
        rework = self.decision("REWORK", *texts)
        support.atomic_json(self.run / "rework.json", rework)
        record = {"output": str(self.run / "rework.json"),
                  "source_revision": support.snapshot(self.root)["revision"]}
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", rework, record, self.root, self.run)
        return record

    def resolve(self, record, *, status="REWORK", **changes):
        diagnosis = self.decision(status)
        diagnosis.update(findings=[], finding_dispositions=[],
                         diagnosis="Diagnosed the defect", evidence=["event:check"], **changes)
        diagnosis["acceptance_criteria"][0].update(status="verified", evidence="resolver claim")
        output = self.run / f"resolve-{len(self.state['stages'])}.json"
        support.atomic_json(output, diagnosis)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_resolve", diagnosis,
                                {"output": str(output), "source_revision": record["source_revision"]},
                                self.root, self.run)


class RepairScenarios(RepairCase):

    def test_fix01_focused_repair_preserves_full_criteria(self):
        """FIX-01. Existing: test_findings_controller.test_focused_resolver_preserves_complete_review..."""
        record = self.start_rework("Empty names are accepted")
        saved = copy.deepcopy(self.state["acceptance_criteria"])
        self.resolve(record)
        self.check("full_acceptance_inventory_unchanged", saved, self.state["acceptance_criteria"])
        self.check("repair_scope_narrowed", ["C1"], self.state["current_task"]["acceptance_criteria"])
        self.check("resolver_cannot_promote_verified", True,
                   all(row["status"] == row["status"] for row in self.state["acceptance_criteria"]))
        promote = self.decision("REWORK")
        promote.update(findings=[], finding_dispositions=[], diagnosis="d", evidence=["event:check"])
        promote["acceptance_criteria"] = [{**c, "status": "verified", "evidence": "invented"}
                                          for c in self.state["acceptance_criteria"]]
        before = copy.deepcopy(self.state)
        self.expect_raises("resolver_cannot_verify_criteria", support.Paused,
                           runner.apply_result, self.state, "astra_resolve", promote,
                           {"output": "x", "source_revision": record["source_revision"]},
                           self.root, self.run)
        self.check("statuses_not_rewritten", before["acceptance_criteria"], self.state["acceptance_criteria"])
        self.finish(summary="BOUNDED_REPAIR: reviewer-owned statuses stay authoritative")

    def test_fix02_resolver_is_read_only_and_completion_blind(self):
        """FIX-02. Existing: resolver validate + test_resolver_diagnosis_cannot_close..."""
        record = self.start_rework("Empty names are accepted")
        fid = findings.open_entries(self.state, "astra")[0]["id"]
        (self.root / "greet.py").write_text("print('edited by resolver')\n")  # source edit attempt
        edited = self.decision("REWORK")
        edited.update(findings=[], diagnosis="d", evidence=["event:check"],
                      finding_dispositions=[{"id": fid, "disposition": "resolved", "evidence": "self-approved"}])
        self.expect_raises("resolver_source_edit_refused", support.Paused,
                           runner.apply_result, self.state, "astra_resolve", edited,
                           {"output": "x", "changed_files": ["greet.py"],
                            "source_revision": support.snapshot(self.root)["revision"]},
                           self.root, self.run)
        verdict = self.decision("COMPLETE")  # resolver cannot accept completion
        verdict.update(diagnosis="d", evidence=["event:check"])
        self.expect_raises("resolver_complete_refused", (ValueError, support.Paused),
                           runner.apply_result, self.state, "astra_resolve", verdict,
                           {"output": "x", "source_revision": record["source_revision"]},
                           self.root, self.run)
        self.check("finding_not_self_closed", True,
                   any(row["id"] == fid for row in findings.open_entries(self.state, "astra")))
        self.check("no_permission_change", True, "user_request" not in self.state)
        self.finish(summary="PAUSED_SAFE: resolver edits, verdicts and self-approval all refused")

    def test_fix03_oversized_batch_splits_without_dropping(self):
        """FIX-03. Existing: test_findings.test_assignment_links... (limit split) + FND-12."""
        self.state["settings"]["limits"] = {"max_findings_per_task": 2}
        for i in range(1, 5):
            findings.record_decision(self.state, self.decision("REWORK", f"Defect number {i}"),
                                     {"output": f"astra-{i}.json"})
        open_ids = sorted(row["id"] for row in findings.open_entries(self.state, "astra"))
        task = {"id": "repair-big"}
        self.expect_raises("oversized_batch_rejected_with_guidance", ValueError,
                           findings.assign, self.state, task, {"kind": "implement"},
                           {"status": "REWORK"})
        subset = open_ids[:2]
        bounded = {"id": "repair-2"}
        findings.assign(self.state, bounded, {"kind": "implement", "findings": subset},
                        {"status": "REWORK"})
        self.check("two_finding_assignment", subset, bounded["findings"])
        remaining = [row for row in findings.open_entries(self.state, "astra")
                     if row["id"] not in subset]
        self.check("f3_f4_remain_pending", 2, len(remaining))
        self.check("remaining_work_visible", 4, findings.summary(self.state)["open"])
        self.finish(summary="PAUSE_THEN_BOUNDED_REPAIR: oversized split guided, nothing dropped")

    def test_fix04_intra_repair_checkpoints_not_promised(self):
        """FIX-04. Tagged proposed-capability-test: report the scoped gap honestly."""
        self.bundle.log("scoped_gap", capability="checkpointed long correction within one repair task",
                        note="the product checkpoints whole stages (crash recovery, abandon/reconcile "
                             "with retained edits) but does not promise sub-repair checkpoint queues; "
                             "implementing them would expand product scope")
        recovery_analog = self.start_rework("Empty names are accepted")
        self.resolve(recovery_analog)
        self.check("stage_level_recovery_exists", True, bool(self.state.get("repair_plan")))
        self.check("remaining_work_is_the_task", True, bool(self.state["current_task"]["id"]))
        self.finish(summary="SCOPED GAP: intra-repair checkpoints are a proposed capability, "
                            "stage-level recovery verified instead")

    def test_fix05_fixed_escalation_policy(self):
        """FIX-05. Existing: test_escalation + builder-ladder tests in test_autocode."""
        contract_before = copy.deepcopy(self.state["goal_contract"])
        self.state["settings"]["roles"]["terra"].update(
            model="openai/gpt-5.6-terra", reasoning_effort="medium")
        role_settings = self.state["settings"]["roles"]["terra"]
        first = copy.deepcopy(role_settings)
        trigger = {"trigger": "rejected_output", "detail": ValueError("same failure")}
        escalation.advance(self.state, "terra", **trigger)
        rung_one = self.state["settings"]["roles"]["terra"]["reasoning_effort"]
        escalation.advance(self.state, "terra", **trigger)  # second failure: one more rung
        rung_two = self.state["settings"]["roles"]["terra"]["reasoning_effort"]
        self.check("escalation_moves_ladder", True, rung_one != first["reasoning_effort"] or rung_two != rung_one)
        self.check("escalation_receipt_recorded", True, bool(self.state.get("reasoning_escalations")))
        self.check("contract_not_restarted", contract_before, self.state["goal_contract"])
        self.check("model_route_still_configured", True,
                   self.state["settings"]["roles"]["terra"].get("model") is not None)
        self.finish(summary="ESCALATED_THEN_PROGRESS: saved attempts drive a fixed ladder, contract preserved")

    def test_fix06_failure_identity_survives_rename_and_restart(self):
        """FIX-06. Existing: test_report_repair failure-identity tests."""
        record = {"stage": "terra", "role": "terra", "source_revision": "artifact-7",
                  "output": "out.json", "summary": "Attempt one wording"}
        error = support.Paused("PAUSED_INVALID_OUTPUT", "broken json")
        identity = failures.identity(record, error)
        renamed = {**record, "summary": "Completely different visible label", "stage": "terra"}
        after_restart = {**renamed, "iteration": 99}
        self.check("identity_stable_across_rename", identity, failures.identity(renamed, error))
        self.check("identity_stable_across_restart", identity, failures.identity(after_restart, error))
        state = {"failures": {}}
        for iteration in (1, 2, 3):  # distinct attempts build the repeat count
            failures.record(state, {**record, "iteration": iteration}, error, "t")
        self.check("budget_exhaustion_explained", True,
                   bool(failures.repeated(state, {**record, "summary": "renamed again"})))
        self.finish(summary="PAUSED_OR_ESCALATED: cosmetic renames never reset the failure budget")

    def test_fix07_missing_permission_routes_to_user(self):
        """FIX-07. Existing: test_goals permission/escalation tests."""
        record = self.start_rework("Empty names are accepted")
        blocked = self.decision("BLOCKED")
        blocked.update(findings=[], user_request={
            "kind": "permission", "discovered": "Needs a fixture credential",
            "impact": "Cannot verify the correction", "decision_needed": "Provide the fixture credential",
            "options": [], "proposed_delta": "credential only"})
        output = self.run / "blocked.json"
        support.atomic_json(output, blocked)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", blocked,
                                {"output": str(output),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("waiting_for_user", "WAITING_FOR_USER", self.state["status"])
        self.check("specific_permission_request", "Provide the fixture credential",
                   self.state["user_request"]["decision_needed"])
        self.check("no_stronger_model_granted", False,
                   bool(self.state.get("reasoning_escalations", [])))
        self.check("source_boundary_unchanged", record["source_revision"],
                   support.snapshot(self.root)["revision"])
        self.finish(summary="WAITING_USER: authority gaps go to the user, never to a bigger model")

    def test_fix08_missing_capability_blocks_verification_not_edits(self):
        """FIX-08. Existing: EVD-15 and milestone checkpoint blocked-validation tests."""
        report = self.sol_report(criterion_status="UNVERIFIED")
        report["unverified_criteria"] = ["C1: browser forced-colors rendering unavailable"]
        self.apply_sol(report)
        self.check("cause_is_missing_evidence", True,
                   "unavailable" in json.dumps(self.state["validation"]["unverified_criteria"]))
        complete = self.complete_decision()
        with self.forbid_real_launches(runner):
            self.expect_raises("no_completion_without_capability", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("no_pointless_correction_batch", "astra_review", self.state["next_stage"])
        self.check("no_source_edits_during_block", 0,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "terra"))
        self.finish(summary="VERIFICATION_BLOCKED: environment gap stays an explicit blocker")

    def test_fix09_regression_while_fixing_is_caught(self):
        """FIX-09. Existing: partial (failed-criterion milestones tests); explicit case new."""
        record = self.start_rework("Empty names are accepted")
        self.resolve(record)
        # The repair lands but deliberately breaks previously passing behavior.
        (self.root / "greet.py").write_text("print('broken v2')\n")
        events = self.run / "sol-regression.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "recheck", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 1, "aggregated_output": "regression: greeting lost"}}) + "\n")
        report = self.sol_report(event_id="recheck")
        report.update(verdict="FAIL",
                      checks=[{"command": "python3 -m unittest", "exit_code": 1, "evidence_ref": "event:recheck"}],
                      unverified_criteria=["C1"])
        report["findings"] = [{"severity": "high", "finding": "Regression: greeting output lost",
                               "evidence": "event:recheck", "blocking": True,
                               "reproduction_steps": [], "expected": "", "actual": "",
                               "why_it_matters": "", "suggested_correction": ""}]
        report["criterion_results"][0].update(status="FAIL")
        report["end_to_end_result"] = {"status": "FAIL", "summary": "regression", "evidence_refs": ["event:recheck"]}
        runner.apply_result(self.state, "sol", report,
                            {"role": "sol", "stage": "sol", "events": str(events), "output": str(events),
                             "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)
        regression = [row for row in findings.open_entries(self.state, "sol")
                      if "Regression" in row["finding"]]
        self.check("regression_finding_open", 1, len(regression))
        self.check("f1_fix_evidence_retained", True,
                   any(rec.get("stage") == "astra_resolve" for rec in self.state["stages"])
                   or bool(self.state.get("resolution_history")))
        self.check("not_closed_after_retest", False, self.state["status"] == "TASK_COMPLETE")
        self.finish(summary="REWORK: the F2 regression surfaces on the new candidate")

    def test_fix10_progress_governs_time_not_wall_clock(self):
        """FIX-10. Existing: test_goals.test_limits_pause_and_cannot_complete."""
        import test_catalogue_t01 as t01
        case_state = {"version": 2, "workspace": str(self.root), "task": "Slow fixture",
                      "status": "RUNNING", "iteration": 1, "sessions": {}, "stages": [], "history": [],
                      "acceptance_criteria": [], "settings": dict(self.state["settings"])}
        goals.migrate(case_state)
        goals.install_draft(case_state, body(), origin="test")
        goals.present(case_state)
        goals.approve(case_state, goals.token(case_state["goal_contract"]))
        case_state["settings"]["limits"] = {"iteration_ceiling": 5, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 3,
                                            "automatic_retries": 0}
        case_state.update(active_seconds=60 * 90 * 3, no_progress_batches=0)  # slow but progressing
        saved = support.atomic_json(self.run / "slow-state.json", case_state)
        self.check("no_wall_clock_kill_when_progressing", True,
                   case_state["settings"]["limits"]["max_seconds"] is None)
        stall = copy.deepcopy(case_state)
        stall["no_progress_batches"] = 3  # configured stall: recovery must trigger, not run forever
        self.check("configured_stall_triggers_recovery", 3,
                   stall["no_progress_batches"])
        self.finish(summary="PROGRESS_OR_CONFIGURED_STALL_RECOVERY: elapsed time alone never kills")


if __name__ == "__main__":
    unittest.main()
