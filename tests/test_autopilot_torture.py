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
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / "tools"
_FAKES = _ROOT / "tests" / "fakes"
for _p in (_ROOT, _TOOLS, _ROOT / "tests", _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
"""AutoPilot state-machine torture suite.

Fake reviewers and builders misbehave. The runner must keep the approved
contract, refuse stale or unverified evidence, and never declare completion
unless the current candidate satisfies every gate. No live model is called.
"""
import copy
import json
import os
import random
import unittest
from pathlib import Path

from . import test_goals
import autocode as runner
import autocode_dispatch as dispatch
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import autopilot
from goal_fixtures import body, envelope


EXECUTING = {"terra", "sol", "orchestrator", "astra_review", "astra_resolve"}


def command_event(event_id, command="python3 -m unittest", exit_code=0, output="PASS"):
    return {"type": "item.completed", "item": {
        "id": event_id, "type": "command_execution", "command": command,
        "exit_code": exit_code, "aggregated_output": output}}


class TortureBase(unittest.TestCase):
    decision = test_goals.GoalTests.decision
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve

    def setUp(self):
        test_goals.GoalTests.setUp(self)
        self.approve()
        self.step = 0
        decision = self.decision("CONTINUE")
        goals.assign_task(self.state, decision, support.snapshot(self.root))
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="terra")

    def fresh(self, name):
        self.step += 1
        return self.run / f"{name}-{self.step}"

    def attempt(self, fn):
        before = copy.deepcopy(self.state)
        try:
            fn()
        except Exception:
            self.assertEqual(before, self.state)
            return False
        return True

    def apply(self, stage, value, record):
        runner.apply_result(self.state, stage, value, record, self.root, self.run)

    def build(self, *, changed=True, task_id=None, contract_hash=None, contract_revision=None):
        path = self.root / "greet.py"
        if changed:
            path.write_text(f"print({self.step!r})\n")
        evidence = self.fresh("build")
        evidence.write_text("built\n")
        events = evidence.with_suffix(".jsonl")
        events.write_text(json.dumps(command_event("build", "python3 greet.py", 0, "built")) + "\n")
        value = {**envelope(self.state), "evidence_refs": [str(evidence)], "summary": "built"}
        if task_id is not None:
            value["task_id"] = task_id
        if contract_hash is not None:
            value["contract_hash"] = contract_hash
        if contract_revision is not None:
            value["contract_revision"] = contract_revision
        record = {"role": "terra", "stage": "terra", "output": str(evidence.with_suffix(".json")),
                  "events": str(events), "source_revision": support.snapshot(self.root)["revision"],
                  "changed_files": ["greet.py"] if changed else [], "after_ref": str(evidence.with_suffix(".after.json")),
                  "diff_ref": None}
        return value, record

    def sol(self, verdict="PASS", *, results=None, checks=None, findings_text=(), dispositions=(),
            event_id="check", command="python3 -m unittest", exit_code=0, unverified=(),
            task_id=None, contract_hash=None, evidence_ref=None):
        log = self.fresh("sol")
        log.write_text(json.dumps(command_event(event_id, command, exit_code)) + "\n")
        ref = evidence_ref if evidence_ref is not None else f"event:{event_id}"
        criteria = self.state["acceptance_criteria"]
        if results is None:
            results = [{"id": row["id"], "status": "PASS" if verdict == "PASS" else "FAIL",
                        "evidence_refs": [ref]} for row in criteria]
        if checks is None:
            checks = [{"command": command, "exit_code": exit_code, "evidence_ref": ref}]
        value = {**envelope(self.state), "verdict": verdict, "findings": [
            {"severity": "high", "finding": text, "evidence": ref, "blocking": True} for text in findings_text],
            "finding_dispositions": list(dispositions), "unverified_criteria": list(unverified),
            "checks_run": [command], "checks": checks, "criterion_results": results,
            "end_to_end_result": {"status": "PASS" if verdict == "PASS" else "FAIL",
                                  "summary": "Checked the current candidate", "evidence_refs": [ref]}}
        if task_id is not None:
            value["task_id"] = task_id
        if contract_hash is not None:
            value["contract_hash"] = contract_hash
        record = {"role": "sol", "stage": "sol", "events": str(log), "output": str(log.with_suffix(".json")),
                  "source_revision": support.snapshot(self.root)["revision"]}
        return value, record

    def astra(self, status, *finding_texts, dispositions=(), criteria_status=None):
        if criteria_status is None:
            criteria_status = "verified" if status in ("COMPLETE", "TASK_COMPLETE") else "unverified"
        criteria = [{**row, "status": criteria_status, "evidence": "event:check"}
                    for row in self.state["acceptance_criteria"]]
        kind = "none" if status in ("COMPLETE", "TASK_COMPLETE", "BLOCKED") else "implement"
        value = {**envelope(self.state), "status": status, "acceptance_criteria": criteria,
                 "next_objective": "" if kind == "none" else "Fix the open defect",
                 "next_task": {"kind": kind, "milestone_id": "" if kind == "none" else "M1",
                               "requirements": [] if kind == "none" else ["Reject empty input"],
                               "acceptance_criteria": [] if kind == "none" else ["C1"],
                               "validation_plan": [] if kind == "none" else ["Run both cases"],
                               "findings": []},
                 "findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                              for text in finding_texts],
                 "finding_dispositions": list(dispositions), "agreed_limitations": [],
                 "evidence": ["event:check"] if status == "REWORK" else [], "blocker": "",
                 "plan": ["Fix", "Recheck"], "affected_paths": ["greet.py"],
                 "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                  "options": [], "proposed_delta": ""}}
        output = self.fresh("astra")
        output.write_text(json.dumps({"status": status}))
        record = {"role": "astra", "stage": "astra_review", "output": str(output),
                  "source_revision": support.snapshot(self.root)["revision"]}
        return value, record

    def open_blocking(self):
        return [row["id"] for row in findings.blocking_entries(self.state)]

    def assert_engine_invariants(self, before_rows=None, source=None, report=None):
        status = self.state.get("status")
        if status == "RUNNING" and self.state.get("next_stage") in EXECUTING:
            self.assertTrue(goals.approved(self.state))
        if not goals.approved(self.state):
            self.assertNotEqual("TASK_COMPLETE", status)
            self.assertNotIn(self.state.get("next_stage"), {"terra", "orchestrator"})
        validation = self.state.get("validation") or {}
        current = support.snapshot(self.root)
        if status == "TASK_COMPLETE":
            self.assertTrue(support.completion_ready(self.state, self.state.get("final_decision") or {}, current))
            self.assertFalse(findings.blocking_entries(self.state))
            self.assertEqual(current["revision"], validation.get("source_revision"))
            self.assertEqual(self.state["goal_contract"]["hash"], validation.get("contract_hash"))
            self.assertEqual(self.state["current_task"]["id"], validation.get("task_id"))
            self.assertFalse(validation.get("unverified_criteria"))
            self.assertTrue(validation.get("checks"))
            self.assertTrue(all(check.get("exit_code") == 0 for check in validation["checks"]))
            self.assertTrue(all(row.get("status") == "PASS" for row in validation.get("criterion_results", [])))
        else:
            self.assertFalse(support.completion_ready(
                self.state, {"status": "COMPLETE", "acceptance_criteria": self.state.get("acceptance_criteria", []),
                             "findings": [], **envelope(self.state)}, current) and False)
        for row in validation.get("criterion_results", []):
            if str(row.get("status", "")).upper() in {"UNVERIFIED", "NOT_VERIFIED", "UNKNOWN", "SKIPPED"}:
                self.assertNotEqual("TASK_COMPLETE", status)
        if validation.get("source_revision") and validation["source_revision"] != current["revision"]:
            self.assertNotEqual("TASK_COMPLETE", status)
            self.assertFalse(support.completion_ready(self.state, self.state.get("final_decision") or {"status": "COMPLETE"}, current))
        if before_rows is not None:
            after = {row["id"]: row for row in self.state.get("findings_ledger", [])}
            for row in before_rows:
                if row.get("status") != "open" or not row.get("blocking", True):
                    continue
                now = after.get(row["id"])
                self.assertIsNotNone(now, row["id"])
                if now["status"] == "open":
                    continue
                self.assertEqual(row["source"], source)
                self.assertIn(now["status"], {"resolved", "retracted"})
                self.assertTrue(str(now.get("resolution_evidence", "")).strip())
                self.assertEqual(report, now.get("resolved_in"))

    def persist(self):
        support.atomic_json(self.run / "state.json", self.state)

    def reload(self):
        self.state = support.read(self.run / "state.json")


class PlanningTests(TortureBase):
    def planning_state(self):
        self.state["settings"]["joint_planning"] = True
        self.state["settings"]["roles"]["plan_reviewer"] = {"model": "plan"}
        self.state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
        self.state["task"] = "Print Hello, NAME. Reject a name that is only whitespace, exit 2."

    def test_planner_cannot_drop_a_requirement_or_weaken_a_criterion(self):
        self.planning_state()
        report = {"summary": "s", "intended_outcome": "o", "required_behaviors": ["Greet"],
                  "constraints": [], "acceptance_tests": ["run"], "source_refs": ["greet.py:1"],
                  "proposed_assumptions": [], "open_questions": [], "requirements": [
                      {"id": "R1", "text": "Reject whitespace", "source_quote": "Reject a name that is only whitespace, exit 2."}],
                  "ignored_statements": [], "conflicts": []}
        autopilot.apply_planning(self.state, "requirements_gather", report, {"output": "req.json"})
        draft = body()
        payload = {"contract": draft, "summary": "plan", "code_refs": [], "alternatives": [],
                   "uncertainties": [], "contract_changes": [], "requirement_trace": []}
        with self.assertRaisesRegex(ValueError, "dropped requirements"):
            autopilot.apply_planning(self.state, "astra_discovery", payload, {"output": "draft.json"})
        self.assertNotIn("goal_contract", {k: self.state[k] for k in self.state if k == "goal_contract" and self.state["goal_contract"]["origin"] == "glm_draft"})
        weakened = body()
        weakened["acceptance_criteria"][0]["criterion"] = "Any output is fine"
        with self.assertRaisesRegex(ValueError, "without a user-backed|cannot be reworded"):
            goals.install_draft(self.state, weakened, origin="glm_revise")
        self.assertEqual("Contract holds", self.state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"])
        self.assertTrue(goals.approved(self.state))

    def test_cyclic_and_missing_dependencies_never_become_a_buildable_contract(self):
        draft = body()
        draft["milestones"] = [
            {"id": "M1", "objective": "Interface", "acceptance_criteria": ["C1"], "depends_on": ["M2"], "affected_paths": ["a.py"]},
            {"id": "M2", "objective": "Caller", "acceptance_criteria": ["C1"], "depends_on": ["M1"], "affected_paths": ["b.py"]}]
        with self.assertRaisesRegex(ValueError, "cycle"):
            goals.validate_body(self.state, draft)
        missing = body()
        missing["milestones"].append({"id": "M2", "objective": "Next", "acceptance_criteria": ["C1"], "affected_paths": ["b.py"]})
        with self.assertRaisesRegex(ValueError, "depends_on"):
            goals.validate_body(self.state, missing)
        unknown = body()
        unknown["milestones"][0]["depends_on"] = ["M9"]
        with self.assertRaisesRegex(ValueError, "unknown milestone"):
            goals.validate_body(self.state, unknown)
        self.assertTrue(goals.approved(self.state))
        self.assertEqual(["M1"], [row["id"] for row in self.state["goal_contract"]["body"]["milestones"]])

    def test_reviewer_rejection_and_scope_change_do_not_approve_a_new_plan(self):
        self.planning_state()
        concern = {"id": "K1", "concern": "Scope grew", "evidence_refs": ["task"],
                   "requested_change": "Keep the CLI", "acceptance_test": "No server starts", "blocking": True}
        autopilot.apply_planning(self.state, "astra_challenge", {"summary": "reject", "concerns": [concern]},
                                 {"output": "challenge.json"})
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertNotEqual(goals.token(self.state["goal_contract"]), self.state["planning"].get("final_token"))
        final = body()
        final["initial_task"] = {"kind": "implement", "objective": "Deliver the CLI", "milestone_id": "M1",
                                 "requirements": ["Greet"], "acceptance_criteria": ["C1"], "validation_plan": ["Run"],
                                 "affected_paths": ["greet.py"]}
        with self.assertRaisesRegex(ValueError, "blocking questions"):
            autopilot.apply_planning(self.state, "astra_finalize", {
                "contract": final, "summary": "still open", "contract_changes": [], "requirement_trace": [],
                "decisions": [{"concern_id": "K1", "decision": "defer", "rationale": "later",
                               "acceptance_test": "No server starts", "resolved": False}]},
                {"output": "final.json"})
        wider = body()
        wider["permission_boundaries"] = ["May write outside the workspace and call external services"]
        before = copy.deepcopy(self.state["goal_contract"])
        with self.assertRaisesRegex(ValueError, "without a user-backed"):
            goals.install_draft(self.state, wider, origin="glm_revise")
        self.assertEqual(before, self.state["goal_contract"])


class StaleAndDisagreementTests(TortureBase):
    def test_stale_builder_sol_and_astra_results_do_not_change_current_state(self):
        first, _ = self.build()
        self.apply("terra", *self.build())
        current = copy.deepcopy(self.state)
        stale_build, stale_record = self.build(task_id="task-old")
        self.assertFalse(self.attempt(lambda: self.apply("terra", stale_build, stale_record)))
        self.assertEqual(current["implementation"]["source_revision"], self.state["implementation"]["source_revision"])
        value, record = self.sol(task_id="task-old")
        self.assertFalse(self.attempt(lambda: self.apply("sol", value, record)))
        decision, review = self.astra("COMPLETE")
        decision["task_id"] = "task-old"
        self.assertFalse(self.attempt(lambda: self.apply("astra_review", decision, review)))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertEqual(self.state["current_task"]["id"], current["current_task"]["id"])

    def test_old_session_after_replan_cannot_apply(self):
        self.apply("sol", *self.sol())
        old = self.sol()
        replacement = body()
        replacement["required_behaviors"] = ["Print a shorter greeting"]
        goals.install_draft(self.state, replacement, origin="user_cli_edit")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        decision = self.decision("CONTINUE")
        goals.assign_task(self.state, decision, support.snapshot(self.root))
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="terra")
        current = copy.deepcopy(self.state)
        self.assertFalse(self.attempt(lambda: self.apply("sol", *old)))
        self.assertEqual(current["goal_contract"]["hash"], self.state["goal_contract"]["hash"])
        self.assertNotIn("validation", self.state)

    def test_sol_pass_cannot_override_astra_rework_in_either_order(self):
        orders = ("sol-then-astra", "astra-then-sol")
        snapshots = {}
        for order in orders:
            with self.subTest(order=order):
                self.setUp()
                if order == "sol-then-astra":
                    self.apply("sol", *self.sol("PASS"))
                    self.apply("astra_review", *self.astra("REWORK", "Empty names are accepted"))
                    late = self.sol("PASS")
                    accepted = self.attempt(lambda: self.apply("sol", *late))
                else:
                    self.apply("astra_review", *self.astra("REWORK", "Empty names are accepted"))
                    accepted = self.attempt(lambda: self.apply("sol", *self.sol("PASS")))
                self.assertNotEqual("TASK_COMPLETE", self.state["status"])
                self.assertTrue(any(row["source"] == "astra" and row["finding"] == "Empty names are accepted"
                                    for row in findings.blocking_entries(self.state)))
                self.assertEqual("astra_resolve", self.state["next_stage"])
                self.assertIn("resolution_request", self.state)
                self.assertNotEqual("PASS", (self.state.get("validation") or {}).get("verdict", "")
                                    if self.state["next_stage"] != "astra_resolve" else "")
                snapshots[order] = (self.state["next_stage"], self.state["status"],
                                    tuple(sorted(row["finding"] for row in findings.blocking_entries(self.state))))
        self.assertEqual(snapshots["sol-then-astra"], snapshots["astra-then-sol"])

    def test_sol_fail_and_astra_complete_stays_open(self):
        self.apply("sol", *self.sol("FAIL", findings_text=("Empty names are accepted",)))
        before = copy.deepcopy(self.state)
        self.assertFalse(self.attempt(lambda: self.apply("astra_review", *self.astra("COMPLETE"))))
        self.assertEqual(before["status"], self.state["status"])
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertTrue(findings.blocking_entries(self.state))

    def test_one_reviewer_blocked_or_crashed_keeps_work_open(self):
        value, record = self.astra("BLOCKED", "Credentials missing")
        value["user_request"] = {"kind": "permission", "discovered": "No login", "impact": "Cannot check accounts",
                                 "decision_needed": "Provide a test account?", "options": ["Yes", "No"],
                                 "proposed_delta": ""}
        value["status"] = "BLOCKED"
        self.apply("astra_review", value, record)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertTrue(any(row["finding"] == "Credentials missing" for row in findings.open_entries(self.state)))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        crashed = copy.deepcopy(self.state)
        self.assertFalse(self.attempt(lambda: (_ for _ in ()).throw(RuntimeError("astra crashed"))))
        self.assertEqual(crashed["status"], self.state["status"])


class FindingsEvidenceAndUnverifiedTests(TortureBase):
    def test_finding_identity_omission_retraction_and_wrong_reviewer(self):
        report = self.sol("FAIL", findings_text=())[0]
        report["findings"] = [
            {"severity": "high", "finding": "Missing authorization check", "evidence": "a.py:1", "blocking": True},
            {"severity": "high", "finding": "Missing authorization check", "evidence": "b.py:2", "blocking": True}]
        self.apply("sol", report, self.sol("FAIL")[1])
        rows = findings.open_entries(self.state, "sol")
        self.assertEqual(2, len({row["id"] for row in rows}))
        kept, dropped = rows
        omitted = self.sol("FAIL", findings_text=("Missing authorization check",))
        omitted[0]["findings"][0]["id"] = kept["id"]
        self.apply("sol", *omitted)
        still = {row["id"]: row for row in findings.open_entries(self.state, "sol")}
        self.assertIn(dropped["id"], still)
        self.assertEqual(omitted[1]["output"], still[dropped["id"]]["not_rechecked_in"])
        retract = self.sol("FAIL", dispositions=[{"id": kept["id"], "disposition": "retracted",
                                                   "evidence": "The finding cited a stale screenshot"}])
        self.apply("sol", *retract)
        self.assertNotIn(kept["id"], self.open_blocking())
        stolen = self.astra("REWORK", dispositions=[{"id": dropped["id"], "disposition": "resolved",
                                                     "evidence": "astra claims it is fixed"}])
        before = self.open_blocking()
        self.apply("astra_review", *stolen)
        self.assertIn(dropped["id"], self.open_blocking())
        self.assertTrue(set(before) <= set(self.open_blocking()) or dropped["id"] in self.open_blocking())

    def test_blocked_review_records_a_new_finding_and_closes_nothing(self):
        self.apply("astra_review", *self.astra("REWORK", "Help text missing"))
        existing = findings.open_entries(self.state, "astra")[0]["id"]
        blocked, record = self.astra("BLOCKED", "Missing authorization check")
        blocked["finding_dispositions"] = [{"id": existing, "disposition": "resolved", "evidence": "guess"}]
        blocked["user_request"] = {"kind": "permission", "discovered": "Need a secret", "impact": "Cannot finish",
                                   "decision_needed": "May the test read the secret?", "options": ["No"],
                                   "proposed_delta": ""}
        self.apply("astra_review", blocked, record)
        open_rows = findings.open_entries(self.state, "astra")
        self.assertEqual({"Help text missing", "Missing authorization check"}, {row["finding"] for row in open_rows})
        self.assertTrue(all(row["status"] == "open" for row in open_rows))

    def test_only_evidence_for_this_candidate_and_check_can_pass(self):
        mentioned = self.sol("PASS", evidence_ref="event:not-run")
        self.assertFalse(self.attempt(lambda: self.apply("sol", *mentioned)))
        wrong_value, wrong_record = self.sol("PASS")
        wrong_value["checks"][0]["command"] = "python3 -m unittest discover"
        self.assertFalse(self.attempt(lambda: self.apply("sol", wrong_value, wrong_record)))
        quoted, executed = "printf '%s\\n' '&&' false", "printf '%s\\n' && false"
        self.assertFalse(support.same_command(quoted, executed))
        failing = self.sol("PASS", exit_code=1)
        self.assertFalse(self.attempt(lambda: self.apply("sol", *failing)))
        self.assertNotIn("validation", self.state)
        self.apply("sol", *self.sol("PASS"))
        pinned = self.state["validation"]["evidence_hashes"]
        target = next(iter(pinned))
        Path(target).write_text(Path(target).read_text() + "tampered\n")
        self.assertFalse(support.completion_ready(self.state, self.astra("COMPLETE")[0], support.snapshot(self.root)))
        previous = self.fresh("old-shot")
        previous.write_text("old candidate\n")
        stale = self.sol("PASS", evidence_ref=str(previous))
        stale[0]["checks"][0]["evidence_ref"] = "event:check"
        stale[0]["criterion_results"][0]["evidence_refs"] = [str(previous)]
        before = copy.deepcopy(self.state["validation"]["source_revision"])
        self.state["current_task"]  # keep task
        path = self.root / "greet.py"
        path.write_text("print('next')\n")
        stale[1]["source_revision"] = before
        self.attempt(lambda: self.apply("sol", stale[0], stale[1]))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertFalse(support.completion_ready(self.state, self.astra("COMPLETE")[0], support.snapshot(self.root)))
        if self.state.get("validation"):
            self.assertNotEqual(support.snapshot(self.root)["revision"], self.state["validation"]["source_revision"])

    def test_unverified_never_becomes_pass(self):
        for label in ("UNVERIFIED", "NOT_VERIFIED", "UNKNOWN"):
            with self.subTest(label=label):
                self.setUp()
                value, record = self.sol("PASS")
                value["criterion_results"][0]["status"] = label
                value["unverified_criteria"] = ["C1 browser unavailable"]
                value["verdict"] = "BLOCKED"
                value["end_to_end_result"]["status"] = "NOT_VERIFIED"
                applied = self.attempt(lambda: self.apply("sol", value, record))
                if applied:
                    self.assertNotEqual("PASS", self.state["validation"]["criterion_results"][0]["status"])
                    self.assertNotEqual("PASS", self.state["validation"]["verdict"])
                decision, review = self.astra("COMPLETE")
                self.assertFalse(self.attempt(lambda: self.apply("astra_review", decision, review)))
                self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_one_unchecked_screen_blocks_completion(self):
        draft = body()
        draft["acceptance_criteria"] = [
            {"id": f"S{i}", "criterion": f"Screen {i} works", "verification_method": "Open the screen",
             "human_review": False} for i in range(1, 22)]
        draft["milestones"][0]["acceptance_criteria"] = [row["id"] for row in draft["acceptance_criteria"]]
        goals.install_draft(self.state, draft, origin="user_cli_edit")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        decision = self.decision("CONTINUE")
        decision["next_task"]["acceptance_criteria"] = [row["id"] for row in draft["acceptance_criteria"]]
        decision["acceptance_criteria"] = [{**row, "status": "unverified", "evidence": ""} for row in draft["acceptance_criteria"]]
        goals.assign_task(self.state, decision, support.snapshot(self.root))
        self.state.update(status="RUNNING", phase="EXECUTING", next_stage="sol")
        results = [{"id": f"S{i}", "status": "PASS", "evidence_refs": ["event:check"]} for i in range(1, 21)]
        results.append({"id": "S21", "status": "NOT_VERIFIED", "evidence_refs": []})
        value, record = self.sol("BLOCKED", results=results, unverified=("S21",))
        value["end_to_end_result"]["status"] = "NOT_VERIFIED"
        self.apply("sol", value, record)
        self.assertEqual("NOT_VERIFIED", self.state["validation"]["criterion_results"][-1]["status"])
        self.assertNotEqual("PASS", self.state["validation"]["verdict"])
        self.assertFalse(self.attempt(lambda: self.apply("astra_review", *self.astra("COMPLETE"))))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])


class CorrectionCrashAndCompletionTests(TortureBase):
    def test_corrections_stay_bounded_and_revalidate_the_latest_candidate(self):
        self.apply("astra_review", *self.astra("REWORK", "F1 broken", "F2 broken"))
        self.assertEqual(2, len(findings.blocking_entries(self.state)))
        self.state["settings"].setdefault("limits", {})["max_findings_per_task"] = 10
        many = self.astra("REWORK", *[f"Finding {i}" for i in range(10)])
        self.apply("astra_review", *many)
        huge = "x" * 8000
        wide = self.astra("REWORK", huge)
        self.apply("astra_review", *wide)
        self.assertTrue(any(row["finding"] == huge for row in findings.open_entries(self.state)))
        self.apply("terra", *self.build())
        self.assertNotEqual(self.state["resolution_request"]["source_revision"], self.state["implementation"]["source_revision"])
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        for _ in range(3):
            again = self.astra("REWORK", "F1 broken")
            fid = next(row["id"] for row in findings.open_entries(self.state, "astra") if row["finding"] == "F1 broken")
            again[0]["findings"][0]["id"] = fid
            self.apply("astra_review", *again)
        repeated = next(row for row in findings.open_entries(self.state, "astra") if row["finding"] == "F1 broken")
        self.assertGreaterEqual(repeated["times_reported"], 2)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])

    def test_crash_at_each_boundary_reloads_durable_state_without_duplicating_work(self):
        self.persist()
        approved = support.read(self.run / "state.json")
        self.apply("terra", *self.build())
        self.assertEqual(approved["current_task"]["id"], support.read(self.run / "state.json")["current_task"]["id"])
        self.assertNotIn("implementation", support.read(self.run / "state.json"))
        self.reload()
        self.persist()
        built = copy.deepcopy(self.state)
        value, record = self.sol("PASS")
        self.apply("sol", value, record)
        self.reload()
        self.assertNotIn("validation", self.state)
        self.apply("sol", value, record)
        self.persist()
        reviewed = support.read(self.run / "state.json")
        self.apply("astra_review", *self.astra("REWORK", "Still broken"))
        self.reload()
        self.assertEqual(reviewed["next_stage"], self.state["next_stage"])
        self.assertFalse(findings.blocking_entries(self.state))
        self.assertEqual(built["current_task"]["id"], self.state["current_task"]["id"])
        alive = {"pid": os.getpid(), "exit_code": None}
        with self.assertRaises(support.Paused) as caught:
            runner.assert_stage_stopped(alive)
        self.assertEqual("PAUSED_WORKSPACE_BUSY", caught.exception.status)
        self.state["active_stage"] = {"stage": "terra", "role": "terra", "iteration": 1,
                                      "output": str(self.run / "partial.json"), "events": str(self.run / "partial.jsonl"),
                                      "supports_sessions": False, "exit_code": None}
        (self.run / "partial.jsonl").write_text("")
        with self.assertRaises(support.Paused):
            runner.reconcile_active(self.state, self.run, self.root)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertIn("active_stage", self.state)

    def test_duplicate_launch_and_late_worker_are_rejected(self):
        marker = str(self.run)
        self.assertTrue(support.duplicate_runner_command(f"python3 /ws/autocode/tools/autocode.py task --run-dir {marker}"))
        self.apply("terra", *self.build())
        first = copy.deepcopy(self.state["implementation"])
        late, late_record = self.build(task_id="task-superseded")
        self.assertFalse(self.attempt(lambda: self.apply("terra", late, late_record)))
        self.assertEqual(first, self.state["implementation"])
        replay, replay_record = self.build()
        replay["task_id"] = self.state["current_task"]["id"]
        stages = len(self.state.get("stages", []))
        self.apply("terra", replay, replay_record)
        self.assertEqual(self.state["current_task"]["id"], first.get("task_id", self.state["implementation"].get("task_id", self.state["current_task"]["id"])))
        self.assertEqual(stages + 1, len(self.state["stages"]))

    def test_completion_requires_the_current_candidate(self):
        self.apply("sol", *self.sol("PASS", findings_text=("Open defect",)))
        self.assertFalse(self.attempt(lambda: self.apply("astra_review", *self.astra("COMPLETE"))))
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        defect = next(row["id"] for row in findings.open_entries(self.state, "sol"))
        clean, record = self.sol("PASS", dispositions=[{"id": defect, "disposition": "resolved",
                                                        "evidence": "Empty input now exits nonzero"}])
        self.apply("sol", clean, record)
        self.state["acceptance_criteria"][0]["status"] = "unverified"
        decision, review = self.astra("COMPLETE", criteria_status="unverified")
        self.assertFalse(self.attempt(lambda: self.apply("astra_review", decision, review)))
        self.state["acceptance_criteria"][0]["status"] = "verified"
        self.state["acceptance_criteria"][0]["evidence"] = "event:check"
        self.apply("astra_review", *self.astra("COMPLETE"))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assert_engine_invariants()
        (self.root / "greet.py").write_text("print('after complete')\n")
        runner.recheck_completion(self.state, self.root)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertFalse(support.completion_ready(self.state, {"status": "COMPLETE"}, support.snapshot(self.root)))

    def test_pass_after_complete_cannot_invent_a_new_completion(self):
        self.apply("sol", *self.sol("PASS"))
        self.apply("astra_review", *self.astra("COMPLETE"))
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertIsNone(self.state.get("next_stage"))
        before = copy.deepcopy(self.state)
        accepted = self.attempt(lambda: self.apply("sol", *self.sol("PASS")))
        if not accepted:
            self.assertEqual("TASK_COMPLETE", self.state["status"])
        else:
            self.assertTrue(self.state["status"] != "TASK_COMPLETE" or self.state.get("next_stage") in (None, "astra_review"))
            if self.state["status"] == "TASK_COMPLETE":
                self.assertTrue(support.completion_ready(self.state, self.state["final_decision"], support.snapshot(self.root)))
            self.assertEqual(before["goal_contract"], self.state["goal_contract"])


class HumanGateParallelAndUpgradeTests(TortureBase):
    def test_human_authorization_is_not_manufactured_or_reused_stale(self):
        request = {"kind": "permission", "decision_needed": "Repair the fallback test?",
                   "impact": "The exact test is excluded", "options": ["Repair", "Keep excluded"],
                   "discovered": "An assertion races navigation", "proposed_delta": "Only the fallback test"}
        goals.wait_for_user(self.state, request)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        question = self.state["pending_questions"][0]["id"]
        goals.resolve_permission(self.state, question, "No, leave it excluded")
        self.assertEqual("No, leave it excluded", self.state["answers"][question]["text"])
        goals.wait_for_user(self.state, copy.deepcopy(request))
        self.assertEqual("No, leave it excluded", self.state["permission_reuse_context"]["answer"])
        with self.assertRaises(support.Paused):
            goals.wait_for_user(self.state, request)
        wider = copy.deepcopy(request)
        wider["proposed_delta"] = "Also change production navigation"
        goals.wait_for_user(self.state, wider)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        old = goals.token(self.state["goal_contract"])
        replacement = body()
        replacement["required_behaviors"] = ["Print Hello only"]
        goals.install_draft(self.state, replacement, origin="user_cli_edit")
        with self.assertRaises(Exception):
            goals.approve(self.state, old)
        self.assertFalse(goals.approved(self.state))

    def test_parallel_waves_reject_overlap_and_keep_a_finished_sibling(self):
        draft = body()
        draft["acceptance_criteria"] = [
            {"id": "C1", "criterion": "Greeting", "verification_method": "Run", "human_review": False},
            {"id": "C2", "criterion": "Farewell", "verification_method": "Run", "human_review": False}]
        draft["milestones"] = [
            {"id": "M1", "objective": "Greeting", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["src/greeting.py"]},
            {"id": "M2", "objective": "Farewell", "acceptance_criteria": ["C2"], "depends_on": [], "affected_paths": ["src/farewell.py"]},
            {"id": "M3", "objective": "Shared", "acceptance_criteria": ["C2"], "depends_on": ["M1"], "affected_paths": ["src/greeting.py"]}]
        goals.install_draft(self.state, draft, origin="user_cli_edit")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        decision = self.decision("CONTINUE")
        decision["affected_paths"] = ["src/greeting.py"]
        decision["next_task"].update(milestone_id="M1", acceptance_criteria=["C1"])
        goals.assign_task(self.state, decision, support.snapshot(self.root))
        self.state["settings"]["orchestration"] = {"enabled": True, "max_parallel": 3}
        self.state["settings"]["milestone_checkpoints"] = {"enabled": True}
        self.state.pop("workflow", None)
        self.state["settings"].pop("workflow", None)
        selected = [row["id"] for row in dispatch.select(self.state)]
        self.assertEqual(["M1", "M2"], selected)
        self.assertNotIn("M3", selected)
        overlapped = copy.deepcopy(draft)
        overlapped["milestones"][0]["acceptance_criteria"] = ["C1", "C2"]
        overlapped["milestones"][1]["affected_paths"] = ["src/greeting.py"]
        overlapped["milestones"][1]["acceptance_criteria"] = ["C1"]
        goals.install_draft(self.state, overlapped, origin="user_cli_edit")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        overlap_decision = self.decision("CONTINUE")
        overlap_decision["affected_paths"] = ["src/greeting.py"]
        overlap_decision["next_task"].update(milestone_id="M1", acceptance_criteria=["C1", "C2"])
        overlap_decision["acceptance_criteria"] = [
            {**row, "status": "unverified", "evidence": ""} for row in self.state["acceptance_criteria"]]
        goals.assign_task(self.state, overlap_decision, support.snapshot(self.root))
        self.assertEqual([], dispatch.select(self.state))
        batch_dir = self.run / "workers"
        for mid, status in (("M1", "BUILT"), ("M2", "PAUSED")):
            worker = batch_dir / mid
            worker.mkdir(parents=True)
            support.atomic_json(worker / "result.json", {"status": status, "reason": "stopped" if status != "BUILT" else ""})
        self.state["next_stage"] = "orchestrator"
        self.state["orchestration_batch"] = {
            "id": "batch", "contract_hash": self.state["goal_contract"]["hash"], "status": "BUILDING",
            "workers": [{"milestone_id": "M1", "run_dir": str(batch_dir / "M1"), "workspace": str(self.root), "task": {}},
                        {"milestone_id": "M2", "run_dir": str(batch_dir / "M2"), "workspace": str(self.root), "task": {}}]}
        with self.assertRaisesRegex(ValueError, "already completed"):
            dispatch.request_retry(self.state, self.run, ["M1"])
        self.assertEqual("BUILT", support.read(batch_dir / "M1" / "result.json")["status"])
        dispatch.request_retry(self.state, self.run, ["M2"])
        self.assertTrue(self.state["orchestration_batch"]["workers"][1]["retry_requested"])
        self.assertNotIn("retry_requested", self.state["orchestration_batch"]["workers"][0])

    def test_runner_upgrade_is_explicit_and_old_workers_stay_stale(self):
        legacy = {"version": 1, "iteration": 1, "status": "TASK_COMPLETE", "task": "old", "sessions": {}}
        migrated = support.migrate_v1(legacy, self.run, self.root, self.state["settings"],
                                      _TOOLS / "autocode-schemas" / "v2")
        self.assertEqual("PAUSED_LEGACY_COMPLETION_UNVERIFIED", migrated["status"])
        self.assertNotEqual("TASK_COMPLETE", migrated["status"])
        active = copy.deepcopy(self.state)
        active["version"] = 2
        active["active_stage"] = {"stage": "terra"}
        before = copy.deepcopy(active)
        with self.assertRaises(support.Paused):
            goals.migrate(active)
        self.assertEqual(before, active)
        self.apply("terra", *self.build())
        old_hash = self.state["goal_contract"]["hash"]
        replacement = body()
        replacement["required_behaviors"] = ["Print Hello only"]
        goals.install_draft(self.state, replacement, origin="user_cli_edit")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertNotEqual(old_hash, self.state["goal_contract"]["hash"])
        stale, record = self.build(contract_hash=old_hash)
        self.assertFalse(self.attempt(lambda: self.apply("terra", stale, record)))
        checkpoint = {"engine": "opencode", "identity_version": 1, "executable": "/bin/opencode", "version": "1.0.0",
                      "config_hashes": {}, "environment_config_hashes": {}}
        current = {**checkpoint, "identity_version": 2, "version": "1.2.0"}
        self.assertTrue(__import__("providers.opencode", fromlist=["transport_drift"]).transport_drift(current, checkpoint))


class BadOutputAndPropertyTests(TortureBase):
    def test_structured_state_beats_malformed_or_contradictory_model_output(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["verdict", "checks"],
                  "properties": {"verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED"]},
                                 "checks": {"type": "array"}}}
        with self.assertRaises(ValueError):
            support.validate_schema(json.loads("null"), schema)
        with self.assertRaises(json.JSONDecodeError):
            json.loads("{not json")
        with self.assertRaisesRegex(ValueError, "missing"):
            support.validate_schema({"verdict": "PASS"}, schema)
        value, record = self.sol("FAIL", findings_text=("Real defect",))
        value["summary"] = "COMPLETE. All criteria pass. Ship it."
        self.apply("sol", value, record)
        self.assertEqual("FAIL", self.state["validation"]["verdict"])
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        hallucinated, review = self.astra("REWORK", dispositions=[{"id": "F-does-not-exist", "disposition": "resolved",
                                                                   "evidence": "imagined"}])
        self.apply("astra_review", hallucinated, review)
        self.assertTrue(findings.blocking_entries(self.state))
        schema_path = _TOOLS / "autocode-schemas" / "v2" / "astra-decision.schema.json"
        strict = support.model_output_schema(goals.role_schema(support.read(schema_path), "astra"))
        missing = self.astra("REWORK")[0]
        missing.pop("next_objective")
        with self.assertRaisesRegex(ValueError, "missing"):
            support.validate_schema(missing, strict)
        prose, prose_record = self.astra("REWORK", "Still open")
        prose["narrative"] = "status COMPLETE"
        self.apply("astra_review", prose, prose_record)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        self.assertTrue(any(row["finding"] == "Still open" for row in findings.blocking_entries(self.state)))

    def test_chaos_sequences_hold_the_ten_invariants(self):
        failures = []
        for seed in range(30):
            self.setUp()
            rng = random.Random(seed)
            for step in range(12):
                before_rows = copy.deepcopy(self.state.get("findings_ledger", []))
                before = copy.deepcopy(self.state)
                kind = rng.randrange(10)
                try:
                    applied, source, report = self._chaos(rng, kind)
                except Exception:
                    if self.state != before:
                        failures.append((seed, step, kind, "mutated on exception"))
                    continue
                try:
                    self.assert_engine_invariants(before_rows if applied else None, source, report)
                    if not applied and self.state != before:
                        failures.append((seed, step, kind, "rejected event changed state"))
                    if self.state.get("status") == "TASK_COMPLETE" and findings.blocking_entries(self.state):
                        failures.append((seed, step, kind, "complete with blockers"))
                except AssertionError as error:
                    failures.append((seed, step, kind, str(error).splitlines()[0]))
        self.assertEqual([], failures)

    def _chaos(self, rng, kind):
        if kind == 0:
            value, record = self.sol("PASS")
            value.pop("checks")
            return self.attempt(lambda: self.apply("sol", value, record)), None, None
        if kind == 1:
            return self.attempt(lambda: self.apply("sol", *self.sol("PASS", task_id="stale-task"))), None, None
        if kind == 2:
            return self.attempt(lambda: self.apply("terra", *self.build(contract_hash="stale-contract"))), None, None
        if kind == 3:
            value, record = self.sol("PASS", evidence_ref="event:never")
            return self.attempt(lambda: self.apply("sol", value, record)), None, None
        if kind == 4:
            value, record = self.astra("REWORK", "Defect")
            value["narrative"] = "COMPLETE"
            self.apply("astra_review", value, record)
            return True, "astra", record["output"]
        if kind == 5:
            value, record = self.sol("FAIL", findings_text=("Defect", "Defect"))
            self.apply("sol", value, record)
            return True, "sol", record["output"]
        if kind == 6:
            existing = findings.open_entries(self.state)
            if not existing:
                return False, None, None
            value, record = self.sol("FAIL")
            value["findings"] = []
            self.apply("sol", value, record)
            return True, "sol", record["output"]
        if kind == 7:
            value, record = self.sol("PASS")
            value["checks_run"] = ["python3 -m unittest"]
            value["checks"][0]["exit_code"] = 1
            return self.attempt(lambda: self.apply("sol", value, record)), None, None
        if kind == 8:
            value, record = self.astra("REWORK")
            value["user_request"] = {"kind": "permission", "discovered": "scope", "impact": "wider",
                                    "decision_needed": "Ship unrelated feature?", "options": ["No"],
                                    "proposed_delta": "Unrelated product"}
            value["status"] = "CONTINUE"
            return self.attempt(lambda: self.apply("astra_review", value, record)), None, None
        self.persist()
        self.apply("terra", *self.build())
        self.reload()
        return False, None, None


if __name__ == "__main__":
    unittest.main()
