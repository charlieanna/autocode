"""One ledger of reviewer findings: identity, fix task, resolution, and batch limits."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support

SCHEMA_DIR = Path(__file__).resolve().parent / "autocode-schemas" / "v2"


def sol(*texts, severity="high"):
    return {"findings": [{"severity": severity, "finding": text, "evidence": "event:check", "blocking": True,
                          "reproduction_steps": [], "expected": "", "actual": "", "why_it_matters": "",
                          "suggested_correction": ""} for text in texts]}


def astra(status, *texts):
    return {"status": status, "findings": [{"severity": "medium", "finding": text, "evidence": "report"} for text in texts]}


class LedgerTests(unittest.TestCase):
    def test_sol_findings_open_repeat_and_resolve_by_the_next_validation(self):
        state = {}
        findings.record_validation(state, sol("Empty names are accepted", "Help text missing"), {"output": "sol-01.json"})
        rows = findings.open_entries(state)
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["source"] == "sol" and row["opened_in"] == "sol-01.json" for row in rows))
        self.assertEqual(rows[0]["id"], findings.finding_id("sol", "  empty   NAMES are accepted "))
        findings.record_validation(state, sol("Empty names are accepted", severity="critical"), {"output": "sol-02.json"})
        open_rows = findings.open_entries(state)
        self.assertEqual(["Empty names are accepted"], [row["finding"] for row in open_rows])
        self.assertEqual((2, "critical", "sol-02.json"), (open_rows[0]["times_reported"], open_rows[0]["severity"], open_rows[0]["last_reported_in"]))
        resolved = [row for row in state["findings_ledger"] if row["status"] == "resolved"]
        self.assertEqual([("Help text missing", "sol-02.json")], [(row["finding"], row["resolved_in"]) for row in resolved])
        self.assertEqual({"open": 1, "resolved": 1, "repeated": 1}, {k: findings.summary(state)[k] for k in ("open", "resolved", "repeated")})

    def test_astra_findings_are_tracked_separately_and_blocked_does_not_close_them(self):
        state = {}
        findings.record_validation(state, sol("Empty names are accepted"), {"output": "sol-01.json"})
        findings.record_decision(state, astra("REWORK", "Empty names are accepted", "No test for blank input"), {"output": "astra-01.json"})
        self.assertEqual({("sol", "Empty names are accepted"), ("astra", "Empty names are accepted"), ("astra", "No test for blank input")},
                         {(row["source"], row["finding"]) for row in findings.open_entries(state)})
        findings.record_decision(state, {"status": "BLOCKED"}, {"output": "astra-02.json"})
        self.assertEqual(3, len(findings.open_entries(state)))
        findings.record_decision(state, astra("CONTINUE"), {"output": "astra-03.json"})
        self.assertEqual([("sol", "Empty names are accepted")],
                         [(row["source"], row["finding"]) for row in findings.open_entries(state)])
        with self.assertRaisesRegex(ValueError, "severity"):
            findings.record_decision(state, {"status": "REWORK", "findings": [{"severity": "loud", "finding": "x"}]}, {"output": "astra-04.json"})

    def test_assignment_links_open_findings_and_the_saved_limit_splits_large_rework(self):
        state = {"settings": {"limits": {}}}
        findings.record_validation(state, sol("A", "B", "C"), {"output": "sol-01.json"})
        task = {"id": "task-1"}
        findings.assign(state, task, {"kind": "implement"}, {"status": "REWORK"})
        self.assertEqual(3, len(task["findings"]))
        self.assertTrue(all(row["assigned_task"] == "task-1" for row in findings.open_entries(state)))
        self.assertEqual([{"task_id": "task-1", "at": findings.open_entries(state)[0]["assigned_history"][0]["at"]}],
                         findings.open_entries(state)[0]["assigned_history"])
        validate = {"id": "task-2"}
        findings.assign(state, validate, {"kind": "validate"}, {"status": "CONTINUE"})
        self.assertEqual([], validate["findings"])

        state["settings"]["limits"]["max_findings_per_task"] = 2
        with self.assertRaisesRegex(ValueError, "names 3 open findings; the saved limit is 2"):
            findings.assign(state, {"id": "task-3"}, {"kind": "implement"}, {"status": "REWORK"})
        subset = sorted(row["id"] for row in findings.open_entries(state))[:2]
        task = {"id": "task-3"}
        findings.assign(state, task, {"kind": "implement", "findings": subset}, {"status": "REWORK"})
        self.assertEqual(subset, task["findings"])
        self.assertEqual({"task-3", "task-1"}, {row["assigned_task"] for row in findings.open_entries(state)})
        with self.assertRaisesRegex(ValueError, "open ledger IDs"):
            findings.assign(state, {"id": "task-4"}, {"kind": "implement", "findings": ["F-missing"]}, {"status": "REWORK"})
        # CONTINUE tasks are not correction batches; the limit applies to REWORK only.
        findings.assign(state, {"id": "task-5"}, {"kind": "implement"}, {"status": "CONTINUE"})
        state["settings"]["limits"]["max_findings_per_task"] = 0
        findings.assign(state, {"id": "task-6"}, {"kind": "implement"}, {"status": "REWORK"})
        state["settings"]["limits"]["max_findings_per_task"] = -1
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            findings.assign(state, {"id": "task-7"}, {"kind": "implement"}, {"status": "REWORK"})

    def test_astra_decision_schema_accepts_structured_findings_and_task_ids(self):
        legacy = support.read(SCHEMA_DIR / "astra-decision.schema.json")
        schema = goals.role_schema(legacy, "astra")
        strict = support.model_output_schema(schema)
        decision = {"status": "REWORK", "acceptance_criteria": [{"id": "C1", "criterion": "x", "status": "unverified", "evidence": ""}],
                    "evidence": ["event:check"], "next_objective": "Fix", "blocker": "", "plan": [], "affected_paths": ["greet.py"],
                    "contract_revision": 1, "contract_hash": "h", "task_id": "t", "deferred_backlog": [],
                    "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "", "options": [], "proposed_delta": ""},
                    "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["r"], "acceptance_criteria": ["C1"],
                                  "validation_plan": ["v"], "findings": ["F-abc"]},
                    "findings": [{"severity": "high", "finding": "Empty names are accepted", "evidence": "event:check", "blocking": True}],
                    "agreed_limitations": []}
        # The generation schema sent to the model requires every field, blocking included.
        support.validate_schema(decision, strict)
        with self.assertRaisesRegex(ValueError, "missing blocking"):
            support.validate_schema({**decision, "findings": [{k: v for k, v in decision["findings"][0].items() if k != "blocking"}]}, strict)
        # Findings and blocking stay optional in the permissive schema used for saved reports.
        support.validate_schema({**decision, "findings": [{k: v for k, v in decision["findings"][0].items() if k != "blocking"}]}, schema)
        without = {**decision, "next_task": {k: v for k, v in decision["next_task"].items() if k != "findings"}}
        without.pop("findings")
        support.validate_schema(without, schema)
        with self.assertRaises(ValueError):
            support.validate_schema({**decision, "findings": [{"severity": "high", "finding": "x"}]}, schema)

    def test_handoff_lists_both_reviewers_open_findings(self):
        state = {"settings": {"limits": {}}}
        findings.record_validation(state, sol("A"), {"output": "sol-01.json"})
        findings.record_decision(state, astra("REWORK", "B"), {"output": "astra-01.json"})
        findings.assign(state, {"id": "task-1"}, {"kind": "implement"}, {"status": "REWORK"})
        rows = findings.handoff(state)
        self.assertEqual({"sol", "astra"}, {row["source"] for row in rows})
        self.assertEqual({"task-1"}, {row["assigned_task"] for row in rows})
        self.assertEqual({"id", "source", "severity", "finding", "evidence", "blocking", "assigned_task", "times_reported",
                          "milestone", "not_rechecked"}, set(rows[0]))

    def scoped_state(self, milestone_id):
        return {"settings": {"limits": {}},
                "goal_contract": {"body": {
                    "acceptance_criteria": [{"id": cid} for cid in ("C1", "C2", "C3")],
                    "milestones": [{"id": "M1", "objective": "first", "acceptance_criteria": ["C1"], "depends_on": []},
                                   {"id": "M2", "objective": "second", "acceptance_criteria": ["C2"], "depends_on": ["M1"]},
                                   {"id": "M3", "objective": "all", "acceptance_criteria": ["C1", "C2", "C3"], "depends_on": ["M2"]}]}},
                "current_task": {"id": "task-" + milestone_id, "milestone_id": milestone_id}}

    def test_a_review_of_other_work_does_not_close_a_finding(self):
        state = self.scoped_state("M1")
        findings.record_validation(state, sol("M1 layout clips text"), {"output": "sol-m1.json"})
        self.assertEqual({"milestone_id": "M1", "criteria": ["C1"]}, findings.open_entries(state)[0]["scope"])
        # Sol then validates M2 only and has nothing to say about M1.
        state["current_task"] = {"id": "task-M2", "milestone_id": "M2"}
        findings.record_validation(state, sol(), {"output": "sol-m2.json"})
        row = findings.open_entries(state)[0]
        self.assertEqual(("M1 layout clips text", "sol-m2.json"), (row["finding"], row["not_rechecked_in"]))
        self.assertEqual([("M1", True)], [(r["milestone"], r["not_rechecked"]) for r in findings.handoff(state)])
        self.assertEqual(1, findings.summary(state)["not_rechecked"])
        # A review that covers M1's criteria and omits the finding closes it.
        state["current_task"] = {"id": "task-M3", "milestone_id": "M3"}
        findings.record_validation(state, sol(), {"output": "sol-m3.json"})
        self.assertEqual([], findings.open_entries(state))
        resolved = state["findings_ledger"][0]
        self.assertEqual("sol-m3.json", resolved["resolved_in"])
        self.assertNotIn("not_rechecked_in", resolved)

    def test_rechecking_the_same_milestone_and_reporting_again_clears_the_stale_mark(self):
        state = self.scoped_state("M1")
        findings.record_validation(state, sol("M1 layout clips text"), {"output": "sol-1.json"})
        state["current_task"] = {"id": "task-M2", "milestone_id": "M2"}
        findings.record_validation(state, sol(), {"output": "sol-2.json"})
        state["current_task"] = {"id": "task-M1b", "milestone_id": "M1"}
        findings.record_validation(state, sol("M1 layout clips text"), {"output": "sol-3.json"})
        row = findings.open_entries(state)[0]
        self.assertEqual((2, "sol-3.json"), (row["times_reported"], row["last_reported_in"]))
        self.assertNotIn("not_rechecked_in", row)
        findings.record_validation(state, sol(), {"output": "sol-4.json"})
        self.assertEqual([], findings.open_entries(state))

    def test_report_repairs_never_close_findings(self):
        state = self.scoped_state("M1")
        findings.record_validation(state, sol("Empty names are accepted"), {"output": "sol-1.json"})
        findings.record_decision(state, astra("REWORK", "No blank-input test"), {"output": "astra-1.json"})
        findings.record_validation(state, sol(), {"output": "sol-1-repair.json", "report_repaired": True})
        findings.record_decision(state, astra("CONTINUE"), {"output": "astra-1-repair.json", "report_only": True})
        self.assertEqual({("sol", "sol-1-repair.json"), ("astra", "astra-1-repair.json")},
                         {(row["source"], row["not_rechecked_in"]) for row in findings.open_entries(state)})
        # A repair can still add a finding the reformatted report contains.
        findings.record_validation(state, sol("Empty names are accepted", "Help text missing"),
                                   {"output": "sol-2-repair.json", "report_repaired": True})
        self.assertEqual({"Empty names are accepted", "Help text missing"},
                         {row["finding"] for row in findings.open_entries(state, "sol")})

    def test_findings_saved_without_a_scope_close_only_on_a_full_review(self):
        state = self.scoped_state("M1")
        state["findings_ledger"] = [{"id": findings.finding_id("sol", "Legacy finding"), "source": "sol", "severity": "high",
                                     "finding": "Legacy finding", "evidence": "", "blocking": True, "status": "open",
                                     "times_reported": 1, "assigned_task": None}]
        findings.record_validation(state, sol(), {"output": "sol-m1.json"})
        self.assertEqual("sol-m1.json", findings.open_entries(state)[0]["not_rechecked_in"])
        state["current_task"] = {"id": "task-M3", "milestone_id": "M3"}
        findings.record_validation(state, sol(), {"output": "sol-full.json"})
        self.assertEqual([], findings.open_entries(state))

    def test_runs_without_milestones_keep_whole_report_resolution(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [{"id": "C1"}]}}, "current_task": {"id": "t", "milestone_id": ""}}
        findings.record_validation(state, sol("A"), {"output": "sol-1.json"})
        self.assertIsNone(findings.open_entries(state)[0]["scope"])
        findings.record_validation(state, sol(), {"output": "sol-2.json"})
        self.assertEqual([], findings.open_entries(state))


class DashboardLedgerTests(unittest.TestCase):
    def test_monitor_prefers_the_ledger_and_reports_counts(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent / "dashboard"))
        import dashboard_monitor as monitor
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            run.mkdir()
            (run / "state.json").write_text("{}")
            state = {"status": "RUNNING", "next_stage": "terra", "stages": [], "unresolved_findings": [{"severity": "high", "finding": "sol only"}]}
            findings.record_validation(state, sol("Empty names are accepted", "Help text missing"), {"output": "sol-01.json"})
            findings.record_decision(state, astra("REWORK", "No blank-input test"), {"output": "astra-01.json"})
            findings.assign(state, {"id": "task-9"}, {"kind": "implement"}, {"status": "REWORK"})
            findings.record_validation(state, sol("Empty names are accepted"), {"output": "sol-02.json"})
            with patch.object(monitor, "process_table") as probe:
                result = monitor.snapshot(state, run, detailed=True)
            probe.assert_not_called()
            self.assertEqual({"open": 2, "resolved": 1, "repeated": 1, "not_rechecked": 0}, result["findings_summary"])
            self.assertEqual({(None, False)}, {(row["milestone"], row["not_rechecked"]) for row in result["findings"]})
            self.assertEqual({("sol", "Empty names are accepted", "task-9", 2), ("astra", "No blank-input test", "task-9", 1)},
                             {(row["source"], row["finding"], row["assigned_task"], row["times_reported"]) for row in result["findings"]})
            legacy = monitor.snapshot({"status": "RUNNING", "stages": [], "unresolved_findings": [{"severity": "low", "finding": "old"}]},
                                      run, detailed=True, process_snapshot=None)
            self.assertEqual([{"severity": "low", "finding": "old"}], legacy["findings"])
            self.assertNotIn("findings_summary", legacy)


if __name__ == "__main__":
    unittest.main()
