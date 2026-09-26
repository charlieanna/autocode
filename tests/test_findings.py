"""One ledger of reviewer findings: identity, fix task, explicit dispositions, batch limits."""
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
import tempfile
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support

SCHEMA_DIR = _TOOLS / "autocode-schemas" / "v2"


def sol(*texts, severity="high", dispositions=()):
    return {"findings": [{"severity": severity, "finding": text, "evidence": "event:check", "blocking": True,
                          "reproduction_steps": [], "expected": "", "actual": "", "why_it_matters": "",
                          "suggested_correction": ""} for text in texts],
            "finding_dispositions": list(dispositions)}


def astra(status, *texts, dispositions=()):
    return {"status": status, "findings": [{"severity": "medium", "finding": text, "evidence": "report"} for text in texts],
            "finding_dispositions": list(dispositions)}


def resolved(fid, evidence="Reran the failing check; it passes"):
    return {"id": fid, "disposition": "resolved", "evidence": evidence}


def retracted(fid, evidence="The finding was based on a stale screenshot"):
    return {"id": fid, "disposition": "retracted", "evidence": evidence}


class LedgerTests(unittest.TestCase):
    def test_sol_findings_open_repeat_and_close_only_by_explicit_disposition(self):
        state = {}
        findings.record_validation(state, sol("Empty names are accepted", "Help text missing"), {"output": "sol-01.json"})
        rows = findings.open_entries(state)
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["source"] == "sol" and row["opened_in"] == "sol-01.json" for row in rows))
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        # Citing the open id refreshes that defect. The same words without an id are a new one.
        again = sol("Empty names are accepted", severity="critical")
        again["findings"][0]["id"] = rows[0]["id"]
        findings.record_validation(state, again, {"output": "sol-02.json"})
        open_rows = findings.open_entries(state)
        self.assertEqual({"Empty names are accepted", "Help text missing"}, {row["finding"] for row in open_rows})
        refreshed = next(row for row in open_rows if row["finding"] == "Empty names are accepted")
        self.assertEqual((2, "critical", "sol-02.json"), (refreshed["times_reported"], refreshed["severity"], refreshed["last_reported_in"]))
        self.assertNotIn("not_rechecked_in", refreshed)
        stale = next(row for row in open_rows if row["finding"] == "Help text missing")
        self.assertEqual("sol-02.json", stale["not_rechecked_in"])
        self.assertEqual({"open": 2, "resolved": 0, "retracted": 0, "repeated": 1, "not_rechecked": 1},
                         {k: findings.summary(state)[k] for k in ("open", "resolved", "retracted", "repeated", "not_rechecked")})
        # An explicit evidenced disposition closes it; a retraction is a separate outcome.
        findings.record_validation(state, sol(dispositions=[resolved(stale["id"], "help text is rendered; screenshot evidence")]),
                                   {"output": "sol-03.json"})
        findings.record_validation(state, sol(dispositions=[retracted(refreshed["id"])]), {"output": "sol-04.json"})
        self.assertEqual([], findings.open_entries(state))
        by_status = {row["finding"]: (row["status"], row["resolved_in"], row["resolution_evidence"]) for row in state["findings_ledger"]}
        self.assertEqual(("resolved", "sol-03.json", "help text is rendered; screenshot evidence"), by_status["Help text missing"])
        self.assertEqual(("retracted", "sol-04.json", "The finding was based on a stale screenshot"), by_status["Empty names are accepted"])

    def test_dispositions_are_checked_and_repairs_cannot_close(self):
        state = {}
        findings.record_validation(state, sol("Empty names are accepted"), {"output": "sol-01.json"})
        fid = findings.open_entries(state)[0]["id"]
        for bad, message in (
            ([{"id": fid, "disposition": "fixed", "evidence": "x"}], "one of resolved, retracted"),
            ([{"id": fid, "disposition": "resolved", "evidence": "  "}], "need evidence"),
            ([{"id": fid, "disposition": "resolved", "evidence": "x"}], "report-only repair"),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                findings.record_validation(state, sol(dispositions=bad),
                                           {"output": "sol-repair.json", "report_repaired": message == "report-only repair"})
        # A disposition for a finding that was never recorded is a no-op, not an error.
        findings.record_validation(state, sol(dispositions=[resolved("F-never-recorded")]), {"output": "sol-noop.json"})
        self.assertEqual(1, len(findings.open_entries(state)))
        # A repair can still add a finding the reformatted report contains.
        findings.record_validation(state, sol("Empty names are accepted", "Help text missing"),
                                   {"output": "sol-02.json", "report_repaired": True})
        self.assertEqual({"Empty names are accepted", "Help text missing"},
                         {row["finding"] for row in findings.open_entries(state, "sol")})

    def test_astra_findings_are_tracked_separately_and_blocked_does_not_close_them(self):
        state = {}
        findings.record_validation(state, sol("Empty names are accepted"), {"output": "sol-01.json"})
        findings.record_decision(state, astra("REWORK", "Empty names are accepted", "No test for blank input"), {"output": "astra-01.json"})
        self.assertEqual({("sol", "Empty names are accepted"), ("astra", "Empty names are accepted"), ("astra", "No test for blank input")},
                         {(row["source"], row["finding"]) for row in findings.open_entries(state)})
        findings.record_decision(state, {"status": "BLOCKED"}, {"output": "astra-02.json"})
        self.assertEqual(3, len(findings.open_entries(state)))
        # A CONTINUE decision that omits them leaves them open; a disposition closes them.
        findings.record_decision(state, astra("CONTINUE"), {"output": "astra-03.json"})
        self.assertEqual(3, len(findings.open_entries(state)))
        astra_id = next(row["id"] for row in findings.open_entries(state, "astra") if row["finding"] == "No test for blank input")
        findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(astra_id, "blank-input test added and passing")]),
                                 {"output": "astra-04.json"})
        self.assertEqual({("sol", "Empty names are accepted"), ("astra", "Empty names are accepted")},
                         {(row["source"], row["finding"]) for row in findings.open_entries(state)})
        with self.assertRaisesRegex(ValueError, "severity"):
            findings.record_decision(state, {"status": "REWORK", "findings": [{"severity": "loud", "finding": "x"}]}, {"output": "astra-05.json"})

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
        findings.assign(state, {"id": "task-5"}, {"kind": "implement"}, {"status": "CONTINUE"})
        state["settings"]["limits"]["max_findings_per_task"] = 0
        findings.assign(state, {"id": "task-6"}, {"kind": "implement"}, {"status": "REWORK"})
        state["settings"]["limits"]["max_findings_per_task"] = -1
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            findings.assign(state, {"id": "task-7"}, {"kind": "implement"}, {"status": "REWORK"})

    def test_astra_decision_schema_accepts_structured_findings_dispositions_and_task_ids(self):
        legacy = support.read(SCHEMA_DIR / "astra-decision.schema.json")
        schema = goals.role_schema(legacy, "astra")
        strict = support.model_output_schema(schema)
        decision = {"status": "REWORK", "acceptance_criteria": [{"id": "C1", "criterion": "x", "status": "unverified", "evidence": ""}],
                    "evidence": ["event:check"], "next_objective": "Fix", "blocker": "", "plan": [], "affected_paths": ["greet.py"],
                    "contract_revision": 1, "contract_hash": "h", "task_id": "t", "deferred_backlog": [],
                    "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "", "options": [], "proposed_delta": ""},
                    "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["r"], "acceptance_criteria": ["C1"],
                                  "validation_plan": ["v"], "findings": ["F-abc"]},
                    "findings": [{"id": "", "severity": "high", "finding": "Empty names are accepted", "evidence": "event:check", "blocking": True}],
                    "finding_dispositions": [{"id": "F-abc", "disposition": "resolved", "evidence": "event:check"}],
                    "agreed_limitations": []}
        support.validate_schema(decision, strict)
        with self.assertRaisesRegex(ValueError, "missing blocking"):
            support.validate_schema({**decision, "findings": [{k: v for k, v in decision["findings"][0].items() if k != "blocking"}]}, strict)
        support.validate_schema({**decision, "findings": [{k: v for k, v in decision["findings"][0].items() if k != "blocking"}]}, schema)
        without = {**decision, "next_task": {k: v for k, v in decision["next_task"].items() if k != "findings"}}
        without.pop("findings")
        without.pop("finding_dispositions")
        support.validate_schema(without, schema)

    def test_sol_report_schema_accepts_finding_dispositions(self):
        legacy = support.read(SCHEMA_DIR / "sol-report.schema.json")
        schema = goals.role_schema(legacy, "sol")
        strict = support.model_output_schema(schema)
        report = {"verdict": "PASS", "checks_run": ["c"], "findings": [], "unverified_criteria": [],
                  "checks": [{"command": "c", "exit_code": 0, "evidence_ref": "event:check"}],
                  "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}],
                  "end_to_end_result": {"status": "PASS", "summary": "s", "evidence_refs": ["event:check"]},
                  "finding_dispositions": [{"id": "F-abc", "disposition": "resolved", "evidence": "event:check"}],
                  "contract_revision": 1, "contract_hash": "h", "task_id": "t", "deferred_backlog": [],
                  "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "", "options": [], "proposed_delta": ""}}
        support.validate_schema(report, strict)
        support.validate_schema({**report, "finding_dispositions": []}, schema)

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

    def test_a_disposition_must_cover_the_scope_the_finding_was_raised_under(self):
        state = self.scoped_state("M1")
        findings.record_validation(state, sol("M1 layout clips text"), {"output": "sol-m1.json"})
        self.assertEqual({"milestone_id": "M1", "criteria": ["C1"]}, findings.open_entries(state)[0]["scope"])
        state["current_task"] = {"id": "task-M2", "milestone_id": "M2"}
        fid = findings.open_entries(state)[0]["id"]
        with self.assertRaisesRegex(ValueError, "did not review"):
            findings.record_validation(state, sol(dispositions=[resolved(fid)]), {"output": "sol-m2.json"})
        row = findings.open_entries(state)[0]
        self.assertEqual(("M1 layout clips text", "sol-m2.json"), (row["finding"], row["not_rechecked_in"]))
        self.assertEqual([("M1", True)], [(r["milestone"], r["not_rechecked"]) for r in findings.handoff(state)])
        self.assertEqual(1, findings.summary(state)["not_rechecked"])
        state["current_task"] = {"id": "task-M3", "milestone_id": "M3"}
        report = sol(dispositions=[resolved(fid)])
        report['criterion_results'] = [{'id': 'C1', 'status': 'PASS', 'evidence_refs': ['event:check']}]
        findings.record_validation(state, report, {"output": "sol-m3.json"})
        self.assertEqual([], findings.open_entries(state))
        resolved_row = state["findings_ledger"][0]
        self.assertEqual("sol-m3.json", resolved_row["resolved_in"])
        self.assertNotIn("not_rechecked_in", resolved_row)

    def test_initial_plan_records_its_explicit_task_scope_before_assignment(self):
        state = self.scoped_state("M1")
        state.pop("current_task")
        report = astra("CONTINUE", "M1 layout clips text")
        report["next_task"] = {"kind": "implement", "milestone_id": "M1", "acceptance_criteria": ["C1"]}
        findings.record_decision(state, report, {"stage": "astra_plan", "output": "astra-plan.json"})
        self.assertEqual({"milestone_id": "M1", "criteria": ["C1"]}, findings.open_entries(state)[0]["scope"])

    def legacy_initial_plan(self):
        state = self.scoped_state("M1")
        state["goal_contract"].update(hash="approved", revision=1, approval_status="approved")
        task = state["current_task"]
        task.update(kind="implement", acceptance_criteria=["C1"], contract_hash="approved", contract_revision=1)
        initial = copy.deepcopy(state)
        initial.pop("current_task")
        findings.record_decision(initial, astra("CONTINUE", "M1 layout clips text"), {"output": "astra-plan.json"})
        state["findings_ledger"] = initial["findings_ledger"]
        fid = state["findings_ledger"][0]["id"]
        task["findings"] = [fid]
        state["findings_ledger"][0]["assigned_history"] = [{"task_id": task["id"]}]
        state["stages"] = [{"stage": "astra_plan", "output": "astra-plan.json", "task_id": None,
                            "contract_hash": "approved", "contract_revision": 1, "exit_code": 0}]
        return state, fid

    def test_legacy_initial_scope_restored_from_accepted_plan_and_first_task(self):
        state, fid = self.legacy_initial_plan()
        # A later approved brief may retain these exact criteria while adding work.
        state["contract_history"] = [copy.deepcopy(state["goal_contract"])]
        state["goal_contract"].update(hash="expanded", revision=2)
        state["task_archive"] = [copy.deepcopy(state["current_task"])]
        state["current_task"] = {"id": "fresh-M1", "milestone_id": "M1"}
        state["validation"] = {"criterion_results": [
            {"id": "C1", "status": "PASS", "evidence_refs": ["event:fresh-check"]}]}
        findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(fid)]), {"output": "review.json"})
        row = state["findings_ledger"][0]
        self.assertEqual("resolved", row["status"])
        self.assertEqual({"milestone_id": "M1", "criteria": ["C1"]}, row["scope"])
        self.assertEqual("task-M1", row["scope_restored_from"]["task_id"])

    def test_legacy_scope_recovery_refuses_unproven_or_changed_scope(self):
        mutations = {
            "review_stage": lambda s: s["stages"][0].update(stage="astra_review"),
            "existing_task": lambda s: s["stages"][0].update(task_id="earlier-task"),
            "rejected": lambda s: s["stages"][0].update(rejected=True),
            "failed_execution": lambda s: s["stages"][0].update(exit_code=1),
            "wrong_contract": lambda s: s["current_task"].update(contract_hash="different"),
            "unapproved": lambda s: s["goal_contract"].update(approval_status="draft"),
            "not_first_assignment": lambda s: s["findings_ledger"][0]["assigned_history"].insert(0, {"task_id": "missing"}),
            "missing_finding_link": lambda s: s["current_task"].update(findings=[]),
            "out_of_milestone": lambda s: s["current_task"].update(acceptance_criteria=["C2"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state, fid = self.legacy_initial_plan()
                mutate(state)
                with self.assertRaisesRegex(ValueError, "did not review"):
                    findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(fid)]), {"output": "review.json"})
                self.assertIsNone(state["findings_ledger"][0]["scope"])
                self.assertEqual("open", state["findings_ledger"][0]["status"])
        state, fid = self.legacy_initial_plan()
        state["contract_history"] = [copy.deepcopy(state["goal_contract"])]
        state["goal_contract"].update(hash="changed", revision=2)
        state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] = "different meaning"
        findings.restore_initial_plan_scopes(state)
        self.assertIsNone(state["findings_ledger"][0]["scope"])

    def test_recovered_scope_still_requires_fresh_owner_review_in_scope(self):
        state, fid = self.legacy_initial_plan()
        with self.assertRaisesRegex(ValueError, "report-only repair"):
            findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(fid)]),
                                     {"output": "repair.json", "report_repaired": True})
        state["current_task"]["milestone_id"] = "M2"
        with self.assertRaisesRegex(ValueError, "did not review"):
            findings.record_decision(state, astra("CONTINUE", dispositions=[resolved(fid)]), {"output": "M2.json"})
        self.assertEqual("open", state["findings_ledger"][0]["status"])

    def test_archived_draft_requires_exact_historical_approval_event(self):
        for token, restored in (("r1:approved", True), ("r2:approved", False), ("r1:other", False)):
            with self.subTest(token=token):
                state, _ = self.legacy_initial_plan()
                state["goal_contract"]["approval_status"] = "draft"
                state["user_events"] = [{"kind": "goal_approval", "token": token}]
                findings.restore_initial_plan_scopes(state)
                self.assertEqual(restored, state["findings_ledger"][0]["scope"] is not None)

    def test_runs_without_milestones_accept_dispositions(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [{"id": "C1"}]}}, "current_task": {"id": "t", "milestone_id": ""}}
        findings.record_validation(state, sol("A"), {"output": "sol-1.json"})
        self.assertIsNone(findings.open_entries(state)[0]["scope"])
        report = sol(dispositions=[resolved(findings.open_entries(state)[0]["id"])])
        report['criterion_results'] = [{'id': 'C1', 'status': 'PASS', 'evidence_refs': ['event:check']}]
        findings.record_validation(state, report, {"output": "sol-2.json"})
        self.assertEqual([], findings.open_entries(state))

    def test_unverified_resolution_retains_old_and_new_findings(self):
        state = self.scoped_state('M1')
        findings.record_validation(state, sol('Persistence broken'), {'output': 'first'})
        fid = findings.open_entries(state)[0]['id']
        report = sol('New data loss', dispositions=[resolved(fid, 'Source looks fixed; runtime blocked')])
        report['criterion_results'] = [{'id': 'C1', 'status': 'NOT_VERIFIED', 'evidence_refs': ['event:read']}]
        findings.record_validation(state, report, {'output': 'second'})
        self.assertEqual(2, len(findings.open_entries(state)))
        self.assertEqual(['C1'], state['findings_ledger'][0]['pending_resolution']['unverified_criteria'])
        # Retraction is distinct: proving the finding was wrong is not a fix claim.
        findings.record_validation(state, sol(dispositions=[retracted(fid)]), {'output': 'third'})
        self.assertEqual('retracted', state['findings_ledger'][0]['status'])

    def test_generation_schema_pins_identity_without_mutating_input(self):
        schema = goals.role_schema(support.read(SCHEMA_DIR / 'sol-report.schema.json'), 'sol')
        state = {'goal_contract': {'hash': 'exact-hash', 'revision': 4},
                 'current_task': {'id': 'task-4'}, 'findings_ledger': [
                     {'id': 'own', 'source': 'sol', 'status': 'open'},
                     {'id': 'foreign', 'source': 'astra', 'status': 'open'},
                     {'id': 'closed', 'source': 'sol', 'status': 'resolved'}]}
        bound = support.review_generation_schema(schema, state, 'sol')['properties']
        self.assertEqual(['exact-hash'], bound['contract_hash']['enum'])
        self.assertEqual([4], bound['contract_revision']['enum'])
        self.assertEqual(['task-4'], bound['task_id']['enum'])
        self.assertEqual(['', 'own'], bound['findings']['items']['properties']['id']['enum'])
        self.assertNotIn('enum', schema['properties']['contract_hash'])

    def test_completion_cannot_close_unverified_finding_and_fresh_pass_can(self):
        state = self.scoped_state('M1')
        findings.record_decision(state, astra('REWORK', 'Persistence broken'), {'output': 'first'})
        fid = findings.open_entries(state)[0]['id']
        decision = astra('REWORK', dispositions=[resolved(fid)])
        state['validation'] = {'criterion_results': [
            {'id': 'C1', 'status': 'NOT_VERIFIED', 'evidence_refs': ['event:blocked']}]}
        findings.record_decision(state, decision, {'output': 'second'})
        self.assertEqual('open', state['findings_ledger'][0]['status'])
        state['validation']['criterion_results'][0]['status'] = 'PASS'
        state['validation']['criterion_results'][0]['evidence_refs'] = ['event:rerun']
        findings.record_decision(state, decision, {'output': 'third'})
        self.assertEqual('resolved', state['findings_ledger'][0]['status'])
        self.assertNotIn('pending_resolution', state['findings_ledger'][0])

    def test_blocked_validator_cannot_close_even_with_passing_subset(self):
        state = self.scoped_state('M1')
        findings.record_validation(state, sol('Persistence broken'), {'output': 'first'})
        fid = findings.open_entries(state)[0]['id']
        report = sol(dispositions=[resolved(fid)])
        report.update(verdict='BLOCKED', criterion_results=[
            {'id': 'C1', 'status': 'PASS', 'evidence_refs': ['event:check']}])
        findings.record_validation(state, report, {'output': 'second'})
        self.assertEqual('open', state['findings_ledger'][0]['status'])

    def test_same_wording_with_different_evidence_stays_two_findings(self):
        state = {}
        report = {"findings": [
            {"severity": "high", "finding": "Missing authorization check", "evidence": "api_a.py:41", "blocking": True},
            {"severity": "high", "finding": "Missing authorization check", "evidence": "api_b.py:72", "blocking": True},
        ], "finding_dispositions": []}
        findings.record_validation(state, report, {"output": "sol-01.json"})
        rows = findings.open_entries(state)
        self.assertEqual(["api_a.py:41", "api_b.py:72"], [row["evidence"] for row in rows])
        self.assertEqual(2, len({row["id"] for row in rows}))
        # Citing one id updates that defect and does not absorb the other.
        repeat = {"findings": [{"id": rows[0]["id"], "severity": "critical", "finding": "Missing authorization check",
                                "evidence": "api_a.py:41", "blocking": True}], "finding_dispositions": []}
        findings.record_validation(state, repeat, {"output": "sol-02.json"})
        self.assertEqual(2, len(findings.open_entries(state)))
        self.assertEqual(2, next(row["times_reported"] for row in findings.open_entries(state) if row["evidence"] == "api_a.py:41"))

    def test_report_repair_preserves_foreign_finding_as_new_owned_entry(self):
        state = {}
        findings.record_decision(state, astra("REWORK", "Tablet cards are too narrow"), {"output": "astra-01.json"})
        astra_id = findings.open_entries(state, "astra")[0]["id"]
        report = sol("Tablet cards are too narrow")
        report["findings"][0]["id"] = astra_id
        with self.assertRaisesRegex(ValueError, "not one open finding of this reviewer"):
            findings.record_validation(state, report, {"output": "sol-01.json"})
        report["findings"][0]["id"] = ""
        findings.record_validation(state, report, {"output": "sol-repair.json", "report_repaired": True})
        self.assertEqual(2, len(findings.open_entries(state)))
        self.assertEqual(astra_id, findings.open_entries(state, "astra")[0]["id"])
        sol_entry = findings.open_entries(state, "sol")[0]
        self.assertNotEqual(astra_id, sol_entry["id"])
        self.assertEqual(report["findings"][0]["evidence"], sol_entry["evidence"])
        self.assertTrue(sol_entry["blocking"])

    def test_blocked_review_records_new_findings_and_closes_nothing(self):
        state = {}
        findings.record_decision(state, astra("REWORK", "Help text missing"), {"output": "astra-01.json"})
        existing = findings.open_entries(state)[0]["id"]
        blocked = astra("BLOCKED", "Missing authorization check")
        blocked["finding_dispositions"] = [resolved(existing)]
        findings.record_decision(state, blocked, {"output": "astra-02.json"})
        open_rows = findings.open_entries(state, "astra")
        self.assertEqual({"Help text missing", "Missing authorization check"}, {row["finding"] for row in open_rows})
        self.assertTrue(all(row["status"] == "open" for row in state["findings_ledger"]))
        self.assertEqual("astra-02.json", next(row["not_rechecked_in"] for row in open_rows if row["id"] == existing))


class DashboardLedgerTests(unittest.TestCase):
    def test_monitor_prefers_the_ledger_and_reports_counts(self):
        import dashboard_monitor as monitor
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            run.mkdir()
            (run / "state.json").write_text("{}")
            state = {"status": "RUNNING", "next_stage": "terra", "stages": [], "unresolved_findings": [{"severity": "high", "finding": "sol only"}]}
            findings.record_validation(state, sol("Empty names are accepted", "Help text missing"), {"output": "sol-01.json"})
            findings.record_decision(state, astra("REWORK", "No blank-input test"), {"output": "astra-01.json"})
            findings.assign(state, {"id": "task-9"}, {"kind": "implement"}, {"status": "REWORK"})
            repeat = sol("Empty names are accepted")
            repeat["findings"][0]["id"] = next(row["id"] for row in findings.open_entries(state, "sol")
                                               if row["finding"] == "Empty names are accepted")
            findings.record_validation(state, repeat, {"output": "sol-02.json"})
            with patch.object(monitor, "process_table") as probe:
                result = monitor.snapshot(state, run, detailed=True)
            probe.assert_not_called()
            self.assertEqual({"open": 3, "resolved": 0, "repeated": 1, "not_rechecked": 1}, result["findings_summary"])
            self.assertEqual({(None, False), (None, True)}, {(row["milestone"], row["not_rechecked"]) for row in result["findings"]})
            legacy = monitor.snapshot({"status": "RUNNING", "stages": [], "unresolved_findings": [{"severity": "low", "finding": "old"}]},
                                      run, detailed=True, process_snapshot=None)
            self.assertEqual([{"severity": "low", "finding": "old"}], legacy["findings"])
            self.assertNotIn("findings_summary", legacy)


if __name__ == "__main__":
    unittest.main()
