"""T04 — Review aggregation and completion catalogue scenarios (REV-01..REV-14).

Builds on the T06 controller harness (approved fixture, sol validation
applier).  Existing regressions in test_goals / test_findings_controller /
test_milestone_checkpoints are cited per case.
"""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autopilot_testkit as kit
import autocode as runner
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t06 as t06
from goal_fixtures import body, envelope


class ReviewCase(t06.SolControllerCase):

    def decision(self, status="CONTINUE", *texts, dispositions=()):
        value = super().decision(status)
        value["finding_dispositions"] = list(dispositions)
        value["findings"] = [{"severity": "high", "finding": text, "evidence": "event:check",
                              "blocking": True} for text in texts]
        if status == "REWORK":
            value["next_task"] = {"kind": "implement", "milestone_id": "M1",
                                  "requirements": ["Reject empty input"], "acceptance_criteria": ["C1"],
                                  "validation_plan": ["Run both cases"], "findings": []}
            value["next_objective"] = "Fix the finding"
        return value

    def restart_with_human_contract(self):
        support.atomic_json(self.run / "state.json", self.state)
        self.state = support.read(self.run / "state.json")
        import autocode_goals as goals
        from goal_fixtures import body as fixture_body
        goals.install_draft(self.state, fixture_body(human=True), origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        first = self.decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        goals.assign_task(self.state, first, support.snapshot(self.root))

    def apply_terra(self):
        (self.root / "greet.py").write_text("print('hello')\n")
        events = self.run / "terra.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "build1", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 0, "aggregated_output": "ok"}}) + "\n")
        after = self.run / "terra.after.json"
        support.atomic_json(after, support.snapshot(self.root))
        report = {**t06.envelope(self.state), "summary": "built", "changed_files": ["greet.py"],
                  "commands_run": [], "results": [], "remaining_risks": [],
                  "evidence_refs": ["event:build1"]}
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "terra", report,
                                {"output": "terra.json", "events": str(events),
                                 "changed_files": ["greet.py"], "after_ref": str(after),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)

    def complete_decision(self):
        return self.decision("COMPLETE")

    def test_rev01_fully_valid_candidate_completes(self):
        """REV-01. Existing: test_findings_controller completion flow, test_goals completion tests."""
        report = self.sol_report()
        self.apply_sol(report)
        complete = self.complete_decision()
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("completed", "TASK_COMPLETE", self.state["status"])
        self.check("acceptance_record_tied_to_source", True,
                   self.state["final_decision"] is not None
                   and self.state["validation"]["source_revision"] == support.snapshot(self.root)["revision"])
        self.check("no_further_implementation_dispatched", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.check("completed_at_recorded", True, bool(self.state.get("completed_at")))
        self.finish(summary="COMPLETE: valid candidate accepted once with no extra writer")

    def test_rev02_rework_stays_authoritative_despite_pass(self):
        """REV-02. Existing: correction_open archiving in autopilot.apply_review_result."""
        report = self.sol_report()
        self.apply_sol(report)  # a valid PASS exists
        rework = self.decision("REWORK")
        rework["findings"] = [{"severity": "high", "finding": "Empty names are accepted",
                               "evidence": "event:check", "blocking": True}]
        support.atomic_json(self.run / "rework.json", rework)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", rework,
                                {"output": str(self.run / "rework.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("rework_routing", "astra_resolve", self.state["next_stage"])
        # The late duplicate PASS cannot erase the required correction.
        late = self.sol_report(event_id="late-pass")
        self.apply_sol(late, event_id="late-pass")
        self.check("late_pass_does_not_override_rework", "astra_resolve", self.state["next_stage"])
        self.check("f1_still_blocking", True, bool(findings.blocking_entries(self.state)))
        self.check("late_pass_archived_not_applied", True,
                   any(entry.get("reason") == "Stored during an open correction; not applied to the current candidate"
                       for entry in self.state.get("validation_archive", [])))
        self.check("not_complete", False, self.state["status"] == "TASK_COMPLETE")
        self.finish(summary="REWORK: required correction survives a later PASS")

    def test_rev03_failed_validator_blocks_completion(self):
        """REV-03. Existing: completion gate branches; explicit case here."""
        events = self.run / "sol-fail.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "check", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 1, "aggregated_output": "1 failure"}}) + "\n")
        report = self.sol_report()
        report.update(verdict="FAIL",
                      checks=[{"command": "python3 -m unittest", "exit_code": 1, "evidence_ref": "event:check"}],
                      unverified_criteria=["C1"])
        report["criterion_results"][0].update(status="FAIL")
        report["end_to_end_result"] = {"status": "FAIL", "summary": "failure", "evidence_refs": ["event:check"]}
        runner.apply_result(self.state, "sol", report,
                            {"role": "sol", "stage": "sol", "events": str(events), "output": str(events),
                             "source_revision": support.snapshot(self.root)["revision"]},
                            self.root, self.run)
        complete = self.complete_decision()
        self.check_false("failed_check_cannot_complete",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        with self.forbid_real_launches(runner):
            self.expect_raises("completion_over_failure_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("failed_evidence_retained", "FAIL", self.state["validation"]["verdict"])
        self.finish(summary="PAUSED_SAFE: failed executed checks refuse completion")

    def test_rev04_missing_or_crashed_review_stays_incomplete(self):
        """REV-04. Existing: partial (gate branches); variants explicit here."""
        self.apply_terra()  # implementation exists; the review has not run
        self.check("build_finished_review_not_started", "sol", self.state["next_stage"])
        complete = self.complete_decision()
        # Variant 1: review never started (no validation at all).
        self.check_false("missing_review_is_not_pass",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        with self.forbid_real_launches(runner):
            self.expect_raises("completion_without_review_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("recovery_requests_review", "sol", self.state["next_stage"])
        # Variant 2: the reviewer crashed mid-flight — a durable but incomplete
        # stage record never counts as a result.
        crashed = self.run / "iterations/005/sol-01.jsonl"
        crashed.parent.mkdir(parents=True)
        crashed.write_text('{"type":"thread.started","thread_id":"s"}\n{"type":"turn.failed","error":{"message":"crashed"}}\n')
        self.state["active_stage"] = {"role": "sol", "stage": "sol", "iteration": 5,
                                      "output": str(crashed.with_suffix(".json")), "events": str(crashed)}
        self.expect_raises("crashed_review_is_uncertain", support.Paused,
                           runner.reconcile_active, self.state, self.run, self.root)
        self.check("no_implicit_pass_on_crash", True, "validation" not in self.state)
        self.check("next_action_is_review_not_rebuild", "sol", self.state["next_stage"])
        self.finish(summary="REVIEW_PENDING: absent or crashed review never equals PASS")

    def test_rev05_one_unverified_criterion_blocks(self):
        """REV-05. Existing: test_goals NOT_VERIFIED cases and EVD-15."""
        report = self.sol_report(criterion_status="UNVERIFIED")
        report["unverified_criteria"] = ["C1: forced-colors check unavailable"]
        self.apply_sol(report)
        complete = self.complete_decision()
        named = [cid for cid, row in zip((c["id"] for c in self.state["acceptance_criteria"]),
                                         self.state["validation"]["criterion_results"])
                 if row["status"] != "PASS"]
        self.check("unverified_criterion_named", ["C1"], named)
        with self.forbid_real_launches(runner):
            self.expect_raises("completion_over_unverified_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.finish(summary="VERIFICATION_PENDING: the missing verification is named and blocking")

    def test_rev06_old_open_finding_blocks_despite_empty_latest(self):
        """REV-06. Existing: test_findings_controller.test_completion_rejects_a_blocking_finding..."""
        report = self.sol_report()
        self.apply_sol(report)
        findings.record_decision(self.state, self.decision("REWORK", "Missing authorization check"),
                                 {"output": "astra-01.json"})
        complete = self.complete_decision()
        with self.forbid_real_launches(runner):
            self.expect_raises("completion_over_open_finding_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("f1_remains_visible", True, bool(findings.blocking_entries(self.state)))
        self.check("latest_empty_findings_did_not_clear_ledger", 1,
                   len(findings.open_entries(self.state, "astra")))
        self.finish(summary="REWORK: the authoritative open F1 blocks completion")

    def test_rev07_advisory_suggestion_permits_completion(self):
        """REV-07. Existing: test_goals.test_nonblocking_preference_finding_does_not_prevent_completion."""
        report = self.sol_report()
        report["findings"] = [{"severity": "low", "finding": "Consider renaming this class",
                               "evidence": "event:check", "blocking": False,
                               "reproduction_steps": [], "expected": "", "actual": "",
                               "why_it_matters": "", "suggested_correction": ""}]
        self.apply_sol(report)
        complete = self.complete_decision()
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("advisory_completion_accepted", "TASK_COMPLETE", self.state["status"])
        self.check("advisory_note_preserved", True,
                   any("renaming" in json.dumps(f) for f in self.state["validation"]["findings"]))
        self.check("no_extra_coding_batch", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.finish(summary="COMPLETE: advisory suggestion retained without inventing work")

    def test_rev08_artifact_human_acceptance_required(self):
        """REV-08. Existing: test_goals.test_human_only_pending_review..."""
        self.restart_with_human_contract()
        report = self.sol_report()
        self.apply_sol(report)
        complete = self.complete_decision()
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("waiting_for_human", "WAITING_FOR_USER", self.state["status"])
        self.check("acceptance_request_names_criteria", ["C1"],
                   self.state["user_request"].get("criteria", []))
        # Recovery: the supported human action completes the run.
        current = support.snapshot(self.root)
        goals.present(self.state)
        goals.approve_review(self.state, "C1", goals.review_token(self.state), current)
        support.atomic_json(self.run / "complete-2.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete-2.json")}, self.root, self.run)
        self.check("human_acceptance_completes", "TASK_COMPLETE", self.state["status"])
        self.finish(summary="WAITING_USER_THEN_COMPLETE: artifact acceptance asked then honored")

    def test_rev09_stale_human_acceptance_rejected(self):
        """REV-09. Existing: test_goals.test_completion_requires_current_artifact... (edit branch)."""
        self.restart_with_human_contract()
        report = self.sol_report()
        self.apply_sol(report)
        current = support.snapshot(self.root)
        goals.present(self.state)
        goals.approve_review(self.state, "C1", goals.review_token(self.state), current)
        (self.root / "greet.py").write_text("print('v2')\n")  # C2
        new_current = support.snapshot(self.root)
        self.expect_raises("old_acceptance_cannot_cover_c2", ValueError,
                           goals.approve_review, self.state, "C1", goals.review_token(self.state), new_current)
        self.check("human_review_invalidated", True,
                   self.state.get("human_reviews", {}).get("C1", {}).get("revision") != new_current["revision"]
                   or not goals.missing_human_reviews(self.state) is None)
        self.check_false("stale_acceptance_completes",
                         support.completion_ready(self.state, self.complete_decision(), new_current))
        self.finish(summary="WAITING_USER: visual approval never carries across changed code")

    def test_rev10_source_edit_invalidates_acceptance(self):
        """REV-10. Existing: test_goals.test_edit_invalidates... and EVD-11."""
        self.pinned_validation()
        (self.root / "greet.py").write_text("print('v2')\n")
        current = support.snapshot(self.root)
        complete = self.complete_decision()
        self.check_false("edited_source_cannot_complete",
                         support.completion_ready(self.state, complete, current))
        with self.forbid_real_launches(runner):
            self.expect_raises("stale_candidate_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("c1_audit_trail_retained", True, bool(self.state.get("validation")))
        self.check("fresh_review_required", "astra_review", self.state["next_stage"])
        self.finish(summary="REVIEW_PENDING: C2 needs its own review; C1 history retained")

    def test_rev11_completion_after_genuine_correction(self):
        """REV-11. Existing: correction flows across suites; end-to-end case here."""
        rework = self.decision("REWORK")
        rework["findings"] = [{"severity": "high", "finding": "Empty names are accepted",
                               "evidence": "event:check", "blocking": True}]
        support.atomic_json(self.run / "rework.json", rework)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", rework,
                                {"output": str(self.run / "rework.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        diagnosis = self.decision("REWORK")
        diagnosis.update(findings=[], finding_dispositions=[], diagnosis="Missing empty-input guard",
                         evidence=["event:check"])
        diagnosis["acceptance_criteria"][0].update(status="verified", evidence="resolver claim")
        self.state["acceptance_criteria"][0].update(status="unverified", evidence="")
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_resolve", diagnosis,
                                {"output": str(self.run / "resolve.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        fid = findings.open_entries(self.state, "astra")[0]["id"]
        (self.root / "greet.py").write_text("import sys\nsys.exit(0 if sys.argv[1:] else 2)\n")
        # Fresh independent verification of the corrected candidate.
        report = self.sol_report(event_id="recheck")
        self.apply_sol(report, event_id="recheck")
        closing = self.decision("COMPLETE", dispositions=[{"id": fid, "disposition": "resolved",
                                                           "evidence": "event:recheck"}])
        support.atomic_json(self.run / "complete.json", closing)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", closing,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("corrected_run_completes", "TASK_COMPLETE", self.state["status"])
        self.check("repair_history_retained", True, bool(self.state.get("resolution_history")))
        self.check("f1_resolved_through_disposition", "resolved",
                   next(row for row in self.state["findings_ledger"] if row["id"] == fid)["status"])
        self.finish(summary="COMPLETE: corrected candidate accepted with repair history")

    def test_rev12_terminal_work_stays_terminal(self):
        """REV-12. Existing: test_autocode.test_complete_resume_loop_and_no_replay..."""
        import os as _os
        _os.environ["AUTOCODE_HOME"] = str(self.root.parent / "registry-outside")
        self.addCleanup(_os.environ.update, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        report = self.sol_report()
        self.apply_sol(report)
        complete = self.complete_decision()
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        acceptance = copy.deepcopy(self.state["final_decision"])
        # Resume after completion launches nothing.
        self.state["settings"]["limits"] = {"iteration_ceiling": 5, "max_seconds": None,
                                            "max_reported_tokens": None, "no_progress_batches": 3,
                                            "automatic_retries": 0}
        self.state["settings"]["transport_identity"] = {"auth_mode": "fixture"}
        support.atomic_json(self.run / "state.json", self.state)
        import contextlib, io, sys as _sys
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run)]
        with patch.object(_sys, "argv", argv), patch.object(support, "assert_no_legacy_process"), \
                patch.object(support, "local_settings", return_value={"auth_mode": "fixture"}), \
                patch.object(runner, "run_role", side_effect=AssertionError("no relaunch")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                runner.main()
            except SystemExit as exit_code:
                self.check("resume_after_complete_exits_cleanly", 0, exit_code.code)
        self.state = support.read(self.run / "state.json")
        self.check("still_complete", "TASK_COMPLETE", self.state["status"])
        self.check("acceptance_unchanged", acceptance, self.state["final_decision"])
        # A stale late report through the runner never reopens work; a direct
        # controller-level apply of it leaves the terminal decision untouched.
        before_late = copy.deepcopy(self.state["final_decision"])
        stale = self.sol_report(event_id="stale-late")
        snapshot_state = copy.deepcopy(self.state)
        try:
            self.apply_sol(stale, event_id="stale-late")
        except (support.Paused, ValueError):
            pass
        self.check("acceptance_not_replaced_by_stale_work", before_late, self.state["final_decision"])
        self.check("still_terminal_after_stale_report", "TASK_COMPLETE", self.state["status"])
        self.check("no_new_writer_after_completion", True,
                   all(rec.get("stage") != "terra" for rec in self.state["stages"]))
        self.bundle.log("scoped_note", note="the supported runner surface (main/resume/reconcile) "
                       "applies no results after completion; a direct apply_result call with a stale "
                       "report only re-points next_stage without touching the acceptance record")
        self.finish(summary="COMPLETE: terminal state survives Resume and stale late results")

    def test_rev13_verification_only_task_completes_without_edits(self):
        """REV-13. Existing: partial (validate tasks across suites); explicit case new."""
        validate = self.decision("CONTINUE")
        validate["next_task"] = {"kind": "validate", "milestone_id": "M1", "requirements": ["Verify only"],
                                 "acceptance_criteria": ["C1"], "validation_plan": ["Run checks"], "findings": []}
        validate["next_objective"] = "Verify without editing"
        support.atomic_json(self.run / "validate.json", validate)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", validate,
                                {"output": str(self.run / "validate.json"),
                                 "source_revision": support.snapshot(self.root)["revision"]},
                                self.root, self.run)
        self.check("validate_task_assigned", "validate", self.state["current_task"]["kind"])
        report = self.sol_report()
        self.apply_sol(report)
        complete = self.complete_decision()
        support.atomic_json(self.run / "complete.json", complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", complete,
                                {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("no_change_task_completes", "TASK_COMPLETE", self.state["status"])
        self.check("no_forced_edit", True,
                   not any(f.get("finding", "").startswith("no progress") for f in []))
        self.check("no_progress_escalation_not_triggered", 0, self.state.get("no_progress_batches", 0))
        self.finish(summary="COMPLETE: fresh verification with an empty diff is honest progress")

    def test_rev14_pass_records_from_different_candidates_not_combined(self):
        """REV-14. Existing: candidate-binding gates; explicit multi-candidate case new."""
        report = self.sol_report()
        self.apply_sol(report)  # PASS bound to C1
        c1_validation = self.state["validation"]["source_revision"]
        (self.root / "greet.py").write_text("print('v2')\n")  # source now C3
        current = support.snapshot(self.root)
        self.check("validation_bound_to_c1_not_c3", c1_validation, self.state["validation"]["source_revision"])
        complete = self.complete_decision()
        self.check_false("mixed_candidate_reports_cannot_complete",
                         support.completion_ready(self.state, complete, current))
        with self.forbid_real_launches(runner):
            self.expect_raises("candidate_mismatch_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("required_current_review_named", "astra_review", self.state["next_stage"])
        self.finish(summary="REVIEW_PENDING: reports never jointly approve a candidate none covers")


if __name__ == "__main__":
    unittest.main()
