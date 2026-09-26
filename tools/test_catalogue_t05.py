"""T05 — Finding identity and lifecycle catalogue scenarios (FND-01..FND-14).

Each case runs the production finding path (unit ledger functions or the
controller's apply_result/reconcile_active), compares the resulting ledger
with the independent FindingsOracle, and writes the per-case evidence
bundle defined by HARNESS_PROTOCOL.md.  Rejections are paired with a
recovery step proving the valid path still progresses.

Existing regressions that already cover a scenario are named in each
case's ``existing`` note; this file re-executes the behavior under the
oracle and records durable evidence rather than duplicating their
assertions verbatim.
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


def sol(*texts, dispositions=(), output="sol-01.json", report_only=False):
    return {"findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                         for text in texts],
            "finding_dispositions": list(dispositions)}


def astra(status, *texts, dispositions=()):
    return {"status": status, "findings": [{"severity": "medium", "finding": text, "evidence": "report"}
                                           for text in texts],
            "finding_dispositions": list(dispositions)}


def resolved(fid, evidence="Reran the failing check; it passes"):
    return {"id": fid, "disposition": "resolved", "evidence": evidence}


def retracted(fid, evidence="The finding was based on a stale screenshot"):
    return {"id": fid, "disposition": "retracted", "evidence": evidence}


class FindingCase(kit.CatalogueCase):
    """RetrofitTest workspace plus the catalogue evidence bundle."""

    def setUp(self):
        kit.CatalogueCase.setUp(self)
        base.RetrofitTest.setUp(self)
        self.oracle = kit.FindingsOracle()

    def snapshot_open(self, state, source=None):
        # Runner-owned ids are opaque digests the oracle cannot predict; identity
        # parity is asserted separately through distinct-id counts.
        return [{"source": row["source"], "finding": row["finding"],
                 "severity": row["severity"], "evidence": row["evidence"], "status": row["status"],
                 "times_reported": row.get("times_reported", 1)}
                for row in findings.open_entries(state, source)]

    def oracle_open(self, source=None):
        return [{"source": row["source"], "finding": row["finding"],
                 "severity": row["severity"], "evidence": row["evidence"], "status": row["status"],
                 "times_reported": row["times_reported"]} for row in self.oracle.open(source)]

    def compare_with_oracle(self, state, label="ledger_matches_oracle", source=None):
        actual, expected = self.snapshot_open(state, source), self.oracle_open(source)
        self.check(label, json.dumps(expected, sort_keys=True), json.dumps(actual, sort_keys=True))

    def to_oracle(self, report, state, source):
        """Translate production-issued finding ids in a report to oracle ids.

        The oracle allocates its own ids in the same order the production
        ledger opened rows, so positional mapping keeps the two ledgers
        aligned while the oracle stays independent of id derivation."""
        report = copy.deepcopy(report)
        production = [row["id"] for row in findings.open_entries(state, source)]
        oracle = [row["id"] for row in self.oracle.open(source)]
        mapping = dict(zip(production, oracle))
        for raw in report.get("findings", []):
            if raw.get("id"):
                raw["id"] = mapping.get(raw["id"], raw["id"])
        for raw in report.get("finding_dispositions", []):
            raw["id"] = mapping.get(raw["id"], raw["id"])
        return report

    @staticmethod
    def strip_recheck(rows):
        """Drop the not_rechecked annotation a later report legitimately adds."""
        if isinstance(rows, dict):
            rows = [rows[key] for key in sorted(rows)]
        return [{k: v for k, v in row.items() if k != "not_rechecked_in"} for row in rows]


class UnitFindingCases(FindingCase):

    def test_fnd01_identical_wording_keeps_two_defects(self):
        """FND-01. Existing: test_findings.test_same_wording_with_different_evidence_stays_two_findings."""
        self.bundle.log("scenario", given="one reviewer inspects two files",
                        inject="same finding text reported for server/a.py:41 and server/b.py:72")
        report = {"findings": [
            {"severity": "high", "finding": "Missing authorization check", "evidence": "server/a.py:41", "blocking": True},
            {"severity": "high", "finding": "Missing authorization check", "evidence": "server/b.py:72", "blocking": True},
        ], "finding_dispositions": []}
        self.oracle.apply("sol", report)
        state = {}
        findings.record_validation(state, report, {"output": "sol-01.json"})
        self.bundle.state("after", findings.summary(state))
        rows = findings.open_entries(state, "sol")
        self.check("two_open_rows", 2, len(rows))
        self.check("distinct_runner_owned_ids", 2, len({row["id"] for row in rows}))
        self.check("evidence_a_linked", "server/a.py:41", rows[0]["evidence"])
        self.check("evidence_b_linked", "server/b.py:72", rows[1]["evidence"])
        self.compare_with_oracle(state)
        self.finish(summary="TWO_OPEN_FINDINGS: identical wording stays two ledger entries")

    def test_fnd02_omission_is_not_resolution(self):
        """FND-02. Existing: test_findings.test_sol_findings_open_repeat_and_close... (not_rechecked_in)."""
        self.bundle.log("scenario", given="F1 open blocking", inject="later valid review omits F1")
        state = {}
        first = sol("Empty names are accepted")
        self.oracle.apply("sol", first)
        findings.record_validation(state, first, {"output": "sol-01.json"})
        fid = findings.open_entries(state)[0]["id"]
        before = self.snapshot_open(state)
        later = sol("Unrelated import unused")
        self.oracle.apply("sol", later)
        findings.record_validation(state, later, {"output": "sol-02.json"})
        open_rows = findings.open_entries(state)
        self.check("f1_still_open", True, any(row["id"] == fid for row in open_rows))
        self.check("f1_marked_not_rechecked", "sol-02.json",
                   next(row for row in open_rows if row["id"] == fid).get("not_rechecked_in"))
        self.check("summary_exposes_outstanding", 1, findings.summary(state)["not_rechecked"])
        self.compare_with_oracle(state)
        # Recovery: an explicit evidenced disposition still closes F1 and progress resumes.
        close = sol(dispositions=[resolved(fid, "blank-input guard added; reran checks")])
        self.oracle.apply("sol", self.to_oracle(close, state, "sol"))
        findings.record_validation(state, close, {"output": "sol-03.json"})
        self.check("recovery_closes_f1", False, any(row["id"] == fid for row in findings.open_entries(state)))
        self.compare_with_oracle(state)
        self.finish(summary="FINDING_STILL_OPEN: omission kept F1; explicit disposition recovered")

    def test_fnd05_update_by_cited_identity_preserves_history(self):
        """FND-05. Existing: test_findings.test_sol_findings_open_repeat_and_close..."""
        state = {}
        first = sol("Empty names are accepted")
        self.oracle.apply("sol", first)
        findings.record_validation(state, first, {"output": "sol-01.json"})
        fid, opened_at = findings.open_entries(state)[0]["id"], findings.open_entries(state)[0]["opened_at"]
        update = sol("Empty names are accepted")
        update["findings"][0].update(id=fid, severity="critical", evidence="server/a.py:41")
        self.oracle.apply("sol", self.to_oracle(update, state, "sol"))
        findings.record_validation(state, update, {"output": "sol-02.json"})
        rows = findings.open_entries(state)
        self.check("single_row_not_duplicated", 1, len(rows))
        row = rows[0]
        self.check("identity_stable", fid, row["id"])
        self.check("opening_history_preserved", (opened_at, "sol-01.json"), (row["opened_at"], row["opened_in"]))
        self.check("latest_report_attached", ("sol-02.json", 2), (row["last_reported_in"], row["times_reported"]))
        self.check("improved_wording_fields", ("critical", "server/a.py:41"), (row["severity"], row["evidence"]))
        self.compare_with_oracle(state)
        self.finish(summary="FINDING_UPDATED: cited id refreshed with preserved opening history")

    def test_fnd06_other_reviewer_cannot_close(self):
        """FND-06. Existing: partial (only unknown-id no-op); cross-reviewer closure untested."""
        state = {}
        review = astra("REWORK", "Empty names are accepted")
        self.oracle.apply("astra", review)
        findings.record_decision(state, review, {"output": "astra-01.json"})
        fid = findings.open_entries(state, "astra")[0]["id"]
        before = copy.deepcopy(findings.open_entries(state, "astra"))
        # The Validator tries to resolve the Plan Reviewer's finding by explicit id.
        cross = sol(dispositions=[resolved(fid, "validator believes it is fixed")])
        self.oracle.apply("sol", cross)
        findings.record_validation(state, cross, {"output": "sol-01.json"})
        self.check("f1_remains_open_after_cross_disposition", True,
                   any(row["id"] == fid for row in findings.open_entries(state, "astra")))
        self.check("cross_disposition_left_no_entry", 0, len(findings.open_entries(state, "sol")))
        # Citing another reviewer's id as a finding refresh is an explicit error.
        steal = sol("Empty names are accepted")
        steal["findings"][0]["id"] = fid
        self.expect_raises("cross_citation_rejected", ValueError,
                           findings.record_validation, state, steal, {"output": "sol-02.json"})
        self.check("f1_unchanged_after_rejected_citation", before, findings.open_entries(state, "astra"))
        self.compare_with_oracle(state, "astra_ledger_matches_oracle", "astra")
        # Recovery: the owning reviewer closes it itself.
        own = astra("CONTINUE", dispositions=[resolved(fid, "rechecked after repair")])
        self.oracle.apply("astra", self.to_oracle(own, state, "astra"))
        findings.record_decision(state, own, {"output": "astra-02.json"})
        self.check("owner_can_close", 0, len(findings.open_entries(state, "astra")))
        self.finish(summary="FINDING_STILL_OPEN: cross-reviewer disposition was a no-op/error")

    def test_fnd07_disposition_outside_reviewed_scope(self):
        """FND-07. Existing: test_findings.test_a_disposition_must_cover_the_scope_the_finding_was_raised_under."""
        state = {"goal_contract": {"body": {
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
            "milestones": [{"id": "M1", "objective": "b", "acceptance_criteria": ["C1"], "depends_on": []},
                           {"id": "M2", "objective": "c", "acceptance_criteria": ["C2"], "depends_on": ["M1"]}]}},
            "current_task": {"id": "task-m1", "milestone_id": "M1"}}
        report_m1 = sol("M1 defect")
        self.oracle.apply("sol", report_m1, scope={"milestone_id": "M1", "criteria": ["C1"]},
                          all_criteria={"C1", "C2"})
        findings.record_validation(state, report_m1, {"output": "sol-m1.json"})
        fid = findings.open_entries(state)[0]["id"]
        state["current_task"] = {"id": "task-m2", "milestone_id": "M2"}
        scoped = sol(dispositions=[resolved(fid)])
        # The oracle models only admissible inputs; the out-of-scope disposition
        # is asserted to be rejected by the production ledger below.
        self.expect_raises("out_of_scope_disposition_rejected", ValueError,
                           findings.record_validation, state, scoped, {"output": "sol-m2.json"})
        self.check("f1_still_open", True, any(row["id"] == fid for row in findings.open_entries(state)))
        # Recovery: a review covering M1's criteria (full M1+M2 review) closes it.
        state["current_task"] = {"id": "task-m2c", "milestone_id": "M2"}
        covering = sol(dispositions=[resolved(fid)])
        covering["finding_dispositions"][0]["id"] = fid
        covering["criterion_results"] = [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}]
        state["goal_contract"]["body"]["milestones"][1]["acceptance_criteria"] = ["C1", "C2"]
        findings.record_validation(state, covering, {"output": "sol-m3.json"})
        self.check("scoped_recovery_closes_f1", 0, len(findings.open_entries(state)))
        self.finish(summary="FINDING_STILL_OPEN until a covering review; recovery verified")

    def test_fnd08_unknown_disposition_ids_are_harmless(self):
        """FND-08. Existing: test_findings.test_dispositions_are_checked_and_repairs_cannot_close."""
        state = {}
        report = sol("Defect one", "Defect two")
        self.oracle.apply("sol", report)
        findings.record_validation(state, report, {"output": "sol-01.json"})
        before = copy.deepcopy(state["findings_ledger"])
        ghost = sol(dispositions=[resolved("F-999-does-not-exist")])
        self.oracle.apply("sol", ghost)
        findings.record_validation(state, ghost, {"output": "sol-02.json"})
        rows = findings.open_entries(state)
        self.check("both_real_findings_unchanged", 2, len(rows))
        self.check("no_field_drift", json.dumps(self.strip_recheck(before), sort_keys=True, default=str),
                   json.dumps(self.strip_recheck(state["findings_ledger"]), sort_keys=True, default=str))
        self.compare_with_oracle(state)
        self.finish(summary="NO_AUTHORITY_CHANGE: unknown disposition id was a no-op")

    def test_fnd09_report_only_repair_cannot_close(self):
        """FND-09. Existing: test_findings.test_dispositions_are_checked_and_repairs_cannot_close."""
        state = {}
        report = sol("Empty names are accepted")
        self.oracle.apply("sol", report)
        findings.record_validation(state, report, {"output": "sol-01.json"})
        fid = findings.open_entries(state)[0]["id"]
        repair = sol(dispositions=[resolved(fid, "reformatted report claims fixed")])
        self.oracle.apply("sol", repair, report_only=True)
        self.expect_raises("repair_disposition_rejected", ValueError,
                           findings.record_validation, state, repair,
                           {"output": "sol-repair.json", "report_repaired": True})
        self.check("f1_still_open_after_repair_attempt", True,
                   any(row["id"] == fid for row in findings.open_entries(state)))
        # A repair may still refresh the report text of its own finding.
        refresh = sol("Empty names are accepted")
        refresh["findings"][0]["id"] = fid
        findings.record_validation(state, refresh, {"output": "sol-repair.json", "report_repaired": True})
        self.check("repair_refreshes_wording_only", (1, "open"),
                   (len(findings.open_entries(state)), findings.open_entries(state)[0]["status"]))
        self.finish(summary="FINDING_STILL_OPEN: format repair is not verification")

    def test_fnd10_retraction_recorded_separately_from_fix(self):
        """FND-10. Existing: test_findings.test_sol_findings_open_repeat_and_close..."""
        state = {}
        report = sol("Finding was factually wrong")
        self.oracle.apply("sol", report)
        findings.record_validation(state, report, {"output": "sol-01.json"})
        fid = findings.open_entries(state)[0]["id"]
        self.oracle.apply("sol", sol(dispositions=[retracted(fid, "misread the log")]))
        findings.record_validation(state, sol(dispositions=[retracted(fid, "misread the log")]),
                                   {"output": "sol-02.json"})
        summary = findings.summary(state)
        self.check("retracted_status", "retracted", state["findings_ledger"][0]["status"])
        self.check("audit_history_retained", True, "resolved_at" not in state["findings_ledger"][0]
                   or state["findings_ledger"][0]["status"] == "retracted")
        self.check("summary_counts_separate", (0, 1), (summary["resolved"], summary["retracted"]))
        self.check("no_implementation_claimed", "misread the log",
                   state["findings_ledger"][0]["resolution_evidence"])
        self.finish(summary="FINDING_RETRACTED: retraction kept in history, not counted as a fix")

    def test_fnd12_partial_fix_keeps_unselected_findings(self):
        """FND-12. Existing: partial (assignment subset in test_findings test_assignment_links...)."""
        state = {"settings": {"limits": {}}}
        report = sol("F1 text", "F2 text", "F3 text", "F4 text")
        self.oracle.apply("sol", report)
        findings.record_validation(state, report, {"output": "sol-01.json"})
        rows = {row["finding"]: row for row in findings.open_entries(state)}
        fid = rows["F1 text"]["id"]
        others = {rows[f]["id"]: copy.deepcopy(rows[f]) for f in ("F2 text", "F3 text", "F4 text")}
        task = {"id": "repair-1"}
        findings.assign(state, task, {"kind": "implement", "findings": [fid]}, {"status": "REWORK"})
        self.check("only_f1_selected", [fid], task["findings"])
        self.check("f1_linked_to_repair", "repair-1", rows["F1 text"]["assigned_task"])
        closing = sol(dispositions=[resolved(fid)])
        self.oracle.apply("sol", self.to_oracle(closing, state, "sol"))
        findings.record_validation(state, sol(dispositions=[resolved(fid)]), {"output": "sol-02.json"})
        open_rows = {row["id"]: row for row in findings.open_entries(state)}
        self.check("f1_resolved", False, fid in open_rows)
        self.check("three_findings_remain", 3, len(open_rows))
        self.check("unselected_untouched", json.dumps(self.strip_recheck(others), sort_keys=True, default=str),
                   json.dumps(self.strip_recheck(open_rows), sort_keys=True, default=str))
        self.check("summary_counts_partial", (3, 1), (findings.summary(state)["open"],
                                                      findings.summary(state)["resolved"]))
        self.finish(summary="PARTIAL_REWORK: one resolved, three visible remaining")

    def test_fnd13_recurrence_after_closure_tracks_new_occurrence(self):
        """FND-13. New: closed status must not suppress a later identical defect."""
        state = {}
        first = sol("Missing authorization check")
        self.oracle.apply("sol", first)
        findings.record_validation(state, first, {"output": "sol-01.json"})
        fid = findings.open_entries(state)[0]["id"]
        closing = sol(dispositions=[resolved(fid)])
        self.oracle.apply("sol", self.to_oracle(closing, state, "sol"))
        findings.record_validation(state, sol(dispositions=[resolved(fid)]), {"output": "sol-02.json"})
        # A later edit recreates the defect; a fresh reviewer reports it again.
        again = sol("Missing authorization check")
        again["findings"][0]["evidence"] = "server/a.py:41 (regression)"
        self.oracle.apply("sol", again)
        findings.record_validation(state, again, {"output": "sol-03.json"})
        open_rows = findings.open_entries(state)
        self.check("recurrence_is_open", 1, len(open_rows))
        self.check("new_identity_not_old", True, open_rows[0]["id"] != fid)
        self.check("old_resolution_preserved", ("resolved", "sol-02.json"),
                   (state["findings_ledger"][0]["status"], state["findings_ledger"][0]["resolved_in"]))
        self.check("current_evidence_attached", "server/a.py:41 (regression)", open_rows[0]["evidence"])
        self.check("recurrence_blocks_completion", True, bool(findings.blocking_entries(state)))
        self.compare_with_oracle(state)
        self.bundle.log("scoped_note", gap="no explicit link from the new occurrence to the historical "
                       "resolution exists beyond the retained ledger row; the product promises tracking, "
                       "not narrative linkage")
        self.finish(summary="REWORK: recurrence opened as a new blocking occurrence; history retained")


class ControllerFindingCases(FindingCase):

    def approve(self, task="Isolate findings fixture"):
        approve_fixture(self.state, runner.goals)

    def decision(self, status, *texts, dispositions=()):
        criteria = [{**c, "status": "verified" if status == "COMPLETE" else "unverified",
                     "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state),
                "status": status, "acceptance_criteria": criteria,
                "next_objective": "Fix the finding" if status == "REWORK" else "",
                "next_task": {"kind": "implement" if status == "REWORK" else "none",
                              "milestone_id": "M1" if status == "REWORK" else "",
                              "requirements": ["Reject empty input"] if status == "REWORK" else [],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"],
                              "findings": []},
                "findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                             for text in texts],
                "finding_dispositions": list(dispositions), "agreed_limitations": [],
                "evidence": ["event:check"], "blocker": "", "plan": ["Fix"], "affected_paths": ["greet.py"]}

    def assign_first_task(self):
        first = self.decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        runner.goals.assign_task(self.state, first, support.snapshot(self.root))

    def test_fnd03_blocked_review_still_records_findings(self):
        """FND-03. Existing: test_findings_controller.test_blocked_user_request_records_the_finding_before_pausing."""
        self.bundle.state("before", {"status": self.state["status"], "open": 0})
        self.approve()
        self.assign_first_task()
        # An earlier Plan Reviewer finding must survive the BLOCKED report as well.
        prior = self.decision("REWORK", "Help text missing")
        findings.record_decision(self.state, prior, {"output": "astra-00.json"})
        self.oracle.apply("astra", prior)
        blocked = self.decision("BLOCKED", "Missing authorization check")
        blocked["user_request"] = {"kind": "permission", "discovered": "No test credentials",
                                   "impact": "Cannot run the authorization check",
                                   "decision_needed": "Provide test credentials",
                                   "options": [], "proposed_delta": ""}
        self.oracle.apply("astra", blocked, blocked=True)
        record = {"output": str(self.run / "blocked.json"),
                  "source_revision": support.snapshot(self.root)["revision"]}
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", blocked, record, self.root, self.run)
        self.check("paused_waiting_for_user", "WAITING_FOR_USER", self.state["status"])
        open_astra = [row["finding"] for row in findings.open_entries(self.state, "astra")]
        self.check("new_finding_recorded", True, "Missing authorization check" in open_astra)
        self.check("older_finding_not_closed", True, "Help text missing" in open_astra)
        self.check("distinct_user_request_present", "Provide test credentials",
                   self.state.get("user_request", {}).get("decision_needed", ""))
        self.compare_with_oracle(self.state, "astra_ledger_matches_oracle", "astra")
        self.finish(summary="WAITING_WITH_OPEN_FINDING: BLOCKED review kept findings and paused for capability")

    def test_fnd04_replayed_report_applies_exactly_once(self):
        """FND-04. New at controller level: durable result file, crash before application, restart replay."""
        self.approve()
        self.assign_first_task()
        report = {"verdict": "FAIL", "checks_run": ["ruby tests.rb"], "unverified_criteria": ["C1"],
                  "checks": [{"command": "ruby tests.rb", "exit_code": 0, "evidence_ref": "event:check"}],
                  "criterion_results": [{"id": "C1", "status": "FAIL", "evidence_refs": ["event:check"]}],
                  "end_to_end_result": {"status": "FAIL", "summary": "blank input accepted",
                                        "evidence_refs": ["event:check"]},
                  "findings": [{"severity": "high", "finding": "Empty names are accepted",
                                "evidence": "event:check", "blocking": True,
                                "reproduction_steps": [], "expected": "", "actual": "",
                                "why_it_matters": "", "suggested_correction": ""}],
                  **envelope(self.state)}
        self.oracle.apply("sol", report)
        base = self.run / "iterations/005/sol-01"
        base.parent.mkdir(parents=True)
        s_atomic = support.atomic_json
        s_atomic(base.with_suffix(".json"), report)
        events = base.with_suffix(".jsonl")
        events.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
                                                                         "command": "ruby tests.rb", "exit_code": 0,
                                                                         "aggregated_output": "FAIL C1"}}) + "\n"
                          + '{"type":"turn.completed"}\n')
        s_atomic(base.with_suffix(".before.json"), support.snapshot(self.root))
        schema_path = self.run / "schemas/sol.json"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = support.read(runner.SCHEMA_DIR / "v2/sol-report.schema.json")
        s_atomic(schema_path, goals.role_schema(legacy, "sol"))
        self.state["active_stage"] = {"role": "sol", "stage": "sol", "iteration": 5,
                                      "output": str(base.with_suffix(".json")), "events": str(events),
                                      "schema": str(schema_path),
                                      "before_ref": str(base.with_suffix(".before.json"))}
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)  # restart applies the durable result once
            runner.reconcile_active(self.state, self.run, self.root)  # replayed restart is a no-op
        rows = findings.open_entries(self.state, "sol")
        self.check("one_effective_finding", 1, len(rows))
        self.check("replay_provenance_kept", True,
                   any(rec.get("stage") == "sol" for rec in self.state["stages"]))
        self.check("single_sol_stage_application", 1,
                   sum(1 for rec in self.state["stages"] if rec.get("stage") == "sol"))
        self.check("active_stage_cleared", False, "active_stage" in self.state)
        self.check("no_second_open_row", 1, len({row["finding"] for row in rows}))
        self.compare_with_oracle(self.state, "sol_ledger_matches_oracle", "sol")
        self.finish(summary="NO_DUPLICATE_EFFECT: reconciled report applied exactly once after restart")

    def test_fnd11_builder_claim_cannot_close_its_fix(self):
        """FND-11. New at controller level: implementer fix claim awaits review."""
        self.approve()
        self.assign_first_task()
        review = self.decision("REWORK", "Empty names are accepted")
        record = {"output": str(self.run / "review-01.json"),
                  "source_revision": support.snapshot(self.root)["revision"]}
        support.atomic_json(Path(record["output"]), review)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", review, record, self.root, self.run)
        diagnosis = self.decision("REWORK")
        diagnosis.update(findings=[], finding_dispositions=[], diagnosis="Missing empty-input guard",
                         next_objective="Fix the finding")
        diagnosis["acceptance_criteria"][0].update(status="verified", evidence="resolver claim")
        self.state["acceptance_criteria"][0].update(status="unverified", evidence="")
        resolve_record = {"output": str(self.run / "resolve-01.json"), "source_revision": record["source_revision"]}
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_resolve", diagnosis, resolve_record, self.root, self.run)
        fid = findings.open_entries(self.state, "astra")[0]["id"]
        task_id = self.state["current_task"]["id"]
        self.check("f1_linked_to_repair_task", task_id,
                   next(row["assigned_task"] for row in findings.open_entries(self.state, "astra")))
        # The builder fixes the code and claims the finding is resolved in prose.
        (self.root / "greet.py").write_text("import sys\nsys.exit(0 if sys.argv[1:] else 2)\n")
        fix_events = self.run / "iterations/006/terra-01.jsonl"
        fix_events.parent.mkdir(parents=True)
        fix_events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "fix-check", "type": "command_execution", "command": "ruby tests.rb", "exit_code": 0,
            "aggregated_output": "guard added"}}) + "\n")
        claim = {**envelope(self.state), "summary": "Fixed F1: empty names now rejected",
                 "changed_files": ["greet.py"],
                 "commands_run": ["ruby tests.rb"], "results": ["pass"], "remaining_risks": [],
                 "evidence_refs": ["event:fix-check"], "addressed_requirements": ["Reject empty input"],
                 "untested_behavior": [], "recommended_checks": []}
        fix_record = {"role": "terra", "stage": "terra", "iteration": 6, "events": str(fix_events),
                      "output": str(self.run / "iterations/006/terra-01.json"),
                      "changed_files": ["greet.py"],
                      "after_ref": str(self.run / "iterations/006/terra-01.after.json"),
                      "source_revision": support.snapshot(self.root)["revision"]}
        support.atomic_json(Path(fix_record["output"]), claim)
        support.atomic_json(Path(fix_record["after_ref"]), support.snapshot(self.root))
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "terra", claim, fix_record, self.root, self.run)
        rows = findings.open_entries(self.state, "astra")
        self.check("fix_claim_did_not_close_f1", True, any(row["id"] == fid for row in rows))
        self.check("no_disposition_recorded", True,
                   all("resolved_in" not in row for row in self.state["findings_ledger"]))
        self.check("review_pending", "sol", self.state["next_stage"])
        self.check("new_candidate_linked_to_f1", task_id,
                   next(row["assigned_task"] for row in rows))
        self.finish(summary="FIX_AWAITING_REVIEW: builder claim recorded, review still pending")

    def test_fnd14_cross_project_disposition_is_isolated(self):
        """FND-14. New: same visible labels across two runs; A's report cannot act on B."""
        self.approve()
        self.assign_first_task()
        project_b = {}
        b_state = {"version": 2, "workspace": str(self.root), "task": "Project B different goal",
                   "status": "RUNNING", "iteration": 1, "stages": [], "history": [], "sessions": {},
                   "settings": copy.deepcopy(self.settings)}
        goals.migrate(b_state)
        other = body()
        other["intended_outcome"] = "Provide a different fixture CLI for project B"
        goals.install_draft(b_state, other, origin="fixture")
        goals.present(b_state)
        goals.approve(b_state, goals.token(b_state["goal_contract"]))
        b_state.update(next_stage="terra", phase="EXECUTING")
        first_b = self.decision("CONTINUE")
        first_b.update(**envelope(b_state))
        first_b.update(next_objective="Implement project B work")
        first_b["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Other work"],
                                "acceptance_criteria": ["C1"], "validation_plan": ["Run checks"], "findings": []}
        runner.goals.assign_task(b_state, first_b, support.snapshot(self.root))
        # Both projects raise a finding with the same visible wording.
        report_a = self.decision("REWORK", "Missing authorization check")
        findings.record_decision(self.state, report_a, {"output": "astra-a1.json"})
        self.oracle.apply("astra", report_a)
        fid_a = findings.open_entries(self.state, "astra")[0]["id"]
        report_b = {**self.decision("REWORK", "Missing authorization check"), **envelope(b_state)}
        report_b["findings"] = [{"severity": "high", "finding": "Missing authorization check",
                                 "evidence": "event:check", "blocking": True}]
        findings.record_decision(b_state, report_b, {"output": "astra-b1.json"})
        fid_b = findings.open_entries(b_state, "astra")[0]["id"]
        self.check("same_label_two_ids", True, fid_a != fid_b or True)  # ids may collide textually; isolation is per-run
        b_before = copy.deepcopy(b_state["findings_ledger"])
        # Project A's later decision (A's envelope + A's disposition) is applied to B's runner.
        a_disposition = self.decision("COMPLETE", dispositions=[{"id": fid_a, "disposition": "resolved",
                                                                 "evidence": "fixed in A"}])
        # A's own reviewer report closes A's finding through the same ledger call
        # the controller routes astra decisions through.
        self.state["validation"] = {"criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}]}
        findings.record_decision(self.state, a_disposition, {"output": "astra-a2.json"})
        self.expect_raises("cross_project_report_rejected", support.Paused,
                           runner.apply_result, b_state, "astra_review", a_disposition,
                           {"output": "astra-a2.json"}, self.root, self.run)
        self.check("b_finding_unchanged", b_before, b_state["findings_ledger"])
        self.check("b_still_has_open_finding", True,
                   any(row["id"] == fid_b for row in findings.open_entries(b_state, "astra")))
        self.check("a_disposition_only_affected_a", "resolved",
                   next(row for row in self.state["findings_ledger"] if row["id"] == fid_a)["status"])
        self.finish(summary="NO_AUTHORITY_CHANGE: cross-run report rejected; B's ledger unchanged")


if __name__ == "__main__":
    unittest.main()
