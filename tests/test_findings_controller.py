"""Controller-level findings wiring: the actual apply_result branches, not just the helpers."""
import copy
import json
import unittest
from unittest.mock import patch

from . import test_autocode as base
from goal_fixtures import approve_fixture, seed_greeting_workspace

runner, support = base.runner, base.s
import autocode_findings as findings


class ControllerFindingsTests(unittest.TestCase):
    def setUp(self):
        base.RetrofitTest.setUp(self)
        seed_greeting_workspace(self.root)
        approve_fixture(self.state, runner.goals)
        # A review needs an assigned task: approve the brief and take its first task.
        first = self.astra_decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        runner.lifecycle.assign_task(self.state, first, support.snapshot(self.root))

    def astra_decision(self, status, *finding_texts, dispositions=(), output=None):
        if output:
            (self.run / output).write_text(json.dumps({"status": status}))
        criteria = [{**c, "status": "verified" if status == "COMPLETE" else "unverified", "evidence": "event:check"}
                    for c in self.state["acceptance_criteria"]]
        return {**{"contract_revision": self.state["goal_contract"]["revision"],
                   "contract_hash": self.state["goal_contract"]["hash"], "task_id": self.state.get("current_task", {}).get("id", ""),
                   "deferred_backlog": [],
                   "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                    "options": [], "proposed_delta": ""}},
                "status": status, "acceptance_criteria": criteria,
                "next_objective": "Fix the finding" if status == "REWORK" else "",
                "next_task": {"kind": "implement" if status == "REWORK" else "none",
                              "milestone_id": "M1" if status == "REWORK" else "",
                              "requirements": ["Reject empty input"] if status == "REWORK" else [],
                              "acceptance_criteria": ["C1"] if status == "REWORK" else [],
                              "validation_plan": ["Run both cases"] if status == "REWORK" else [],
                              "findings": []},
                "findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                             for text in finding_texts],
                "finding_dispositions": list(dispositions),
                "agreed_limitations": [], "evidence": ["event:check"], "blocker": "",
                "plan": ["Fix", "Recheck"], "affected_paths": ["greet.py"]}

    def test_rework_registers_findings_before_the_resolver_runs(self):
        decision = self.astra_decision("REWORK", "Empty names are accepted", "No blank-input test", output="review-01.json")
        record = {"output": str(self.run / "review-01.json"), "source_revision": support.snapshot(self.root)["revision"]}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_review", decision, record, self.root, self.run)
        self.assertEqual("astra_resolve", self.state["next_stage"])
        self.assertIn("resolution_request", self.state)
        self.assertEqual({"Empty names are accepted", "No blank-input test"},
                         {row["finding"] for row in findings.open_entries(self.state, "astra")})
        self.assertEqual({"Empty names are accepted", "No blank-input test"},
                         {row["finding"] for row in findings.handoff(self.state)})

    def test_resolver_diagnosis_cannot_close_reviewer_findings(self):
        decision = self.astra_decision("REWORK", "Empty names are accepted", output="review-01.json")
        record = {"output": str(self.run / "review-01.json"), "source_revision": support.snapshot(self.root)["revision"]}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_review", decision, record, self.root, self.run)
        fid = findings.open_entries(self.state, "astra")[0]["id"]
        diagnosis = {**self.astra_decision("REWORK", output="resolve-01.json"), "findings": [],
                     "finding_dispositions": [dict(id=fid, disposition="resolved", evidence="diagnosed")],
                     "diagnosis": "The blank check runs before the greeting is printed",
                     "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Reject empty input"],
                                   "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []},
                     "next_objective": "Fix the finding"}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_resolve", diagnosis,
                                {"output": str(self.run / "resolve-01.json"), "source_revision": record["source_revision"]},
                                self.root, self.run)
        rows = findings.open_entries(self.state, "astra")
        self.assertEqual(["Empty names are accepted"], [row["finding"] for row in rows])
        # The resolver never reconciles: the finding is untouched, not even marked as reviewed.
        self.assertNotIn("not_rechecked_in", rows[0])
        self.assertEqual(1, rows[0]["times_reported"])

    def resolver_fixture(self):
        body = copy.deepcopy(self.state["goal_contract"]["body"])
        body["acceptance_criteria"].append({**body["acceptance_criteria"][0],
                                          "id": "C2", "criterion": "Reject empty names"})
        body["milestones"][0]["acceptance_criteria"].append("C2")
        runner.lifecycle.install_draft(self.state, body, origin="test")
        runner.lifecycle.human.evaluate(self.state)
        runner.lifecycle.present(self.state)
        runner.lifecycle.approve(self.state, runner.goals.token(self.state["goal_contract"]))
        first = self.astra_decision("REWORK")
        runner.lifecycle.assign_task(self.state, first, support.snapshot(self.root))
        review = self.astra_decision("REWORK", "Empty names are accepted", output="review.json")
        record = {"output": str(self.run / "review.json"),
                  "source_revision": support.snapshot(self.root)["revision"]}
        runner.apply_result(self.state, "astra_review", review, record, self.root, self.run)
        diagnosis = {**self.astra_decision("REWORK", output="resolve.json"),
                     "diagnosis": "Missing empty-input guard"}
        diagnosis["acceptance_criteria"] = diagnosis["acceptance_criteria"][:1]
        return diagnosis, {**record, "output": str(self.run / "resolve.json")}

    def test_focused_resolver_preserves_complete_review_and_dispatches_subset(self):
        diagnosis, record = self.resolver_fixture()
        saved = copy.deepcopy(self.state["acceptance_criteria"])
        saved_contract = copy.deepcopy(self.state["goal_contract"])
        saved_blocking = copy.deepcopy(findings.blocking_entries(self.state))
        diagnosis["acceptance_criteria"][0].update(criterion="Weaker requirement",
                                                   verification_method="Glance at the output",
                                                   human_review=True, status="verified",
                                                   evidence="resolver claim")
        original = copy.deepcopy(diagnosis)
        runner.apply_result(self.state, "astra_resolve", diagnosis, record, self.root, self.run)
        self.assertEqual(saved, self.state["acceptance_criteria"])
        self.assertEqual(saved, self.state["last_decision"]["report"]["acceptance_criteria"])
        self.assertEqual(original, diagnosis)
        self.assertEqual(saved_contract, self.state["goal_contract"])
        self.assertEqual(saved_contract["hash"], self.state["goal_contract"]["hash"])
        self.assertEqual(saved_contract["revision"], self.state["goal_contract"]["revision"])
        self.assertEqual("terra", self.state["next_stage"])
        self.assertEqual(["C1"], self.state["current_task"]["acceptance_criteria"])
        self.assertTrue(findings.blocking_entries(self.state))
        self.assertIn("repair_plan", self.state)
        rows = findings.blocking_entries(self.state)
        self.assertEqual(["Empty names are accepted"], [row["finding"] for row in rows])
        row = rows[0]
        saved_row = saved_blocking[0]
        dispatch_fields = ("assigned_task", "assigned_history")
        self.assertEqual({key: value for key, value in row.items() if key not in dispatch_fields},
                         {key: value for key, value in saved_row.items() if key not in dispatch_fields})
        self.assertEqual(self.state["current_task"]["id"], row["assigned_task"])
        prior_history = saved_row.get("assigned_history", [])
        self.assertEqual(prior_history, row["assigned_history"][:-1])
        self.assertEqual(len(prior_history) + 1, len(row["assigned_history"]))
        self.assertEqual(self.state["current_task"]["id"], row["assigned_history"][-1]["task_id"])

    def test_resolver_cannot_add_or_duplicate_criteria(self):
        diagnosis, record = self.resolver_fixture()
        for change, status in (("unknown", "PAUSED_CRITERIA_CHANGE"), ("duplicate", "PAUSED_INVALID_OUTPUT")):
            value = copy.deepcopy(diagnosis)
            if change == "unknown":
                value["acceptance_criteria"][0]["id"] = "C999"
            else:
                value["acceptance_criteria"] *= 2
            before = copy.deepcopy(self.state)
            with self.subTest(change=change), self.assertRaises(support.Paused) as paused:
                runner.apply_result(self.state, "astra_resolve", value, record, self.root, self.run)
            self.assertEqual(status, paused.exception.status)
            self.assertEqual(before, self.state)
        # A restated criterion no longer rejects the report (#624), but it cannot
        # redefine the criterion either: the approved wording is what is kept.
        approved = copy.deepcopy(self.state["acceptance_criteria"])
        contract = copy.deepcopy(self.state["goal_contract"])
        value = copy.deepcopy(diagnosis)
        value["acceptance_criteria"][0]["criterion"] = "Weaker requirement"
        runner.apply_result(self.state, "astra_resolve", value, record, self.root, self.run)
        self.assertEqual("terra", self.state["next_stage"])
        self.assertEqual(approved, self.state["acceptance_criteria"])
        self.assertEqual(contract, self.state["goal_contract"])
        self.assertNotIn("Weaker requirement", json.dumps(self.state))

    def test_a_resolver_that_restates_a_criterion_keeps_the_approved_wording(self):
        # Since #627 (issue 624) a restatement no longer rejects the report; it cannot redefine the criterion.
        diagnosis, record = self.resolver_fixture()
        approved = copy.deepcopy(self.state["acceptance_criteria"])
        diagnosis["acceptance_criteria"][0]["criterion"] = "Weaker requirement"
        runner.apply_result(self.state, "astra_resolve", diagnosis, record, self.root, self.run)
        self.assertEqual(approved, self.state["acceptance_criteria"])
        self.assertEqual(approved, self.state["last_decision"]["report"]["acceptance_criteria"])

    def test_blocked_user_request_records_the_finding_before_pausing(self):
        decision = self.astra_decision("BLOCKED", "Missing authorization check", output="blocked.json")
        decision["user_request"] = {"kind": "permission", "discovered": "No test credentials",
                                    "impact": "Cannot finish the authorization check",
                                    "decision_needed": "Provide test credentials", "options": [], "proposed_delta": ""}
        record = {"output": str(self.run / "blocked.json"), "source_revision": support.snapshot(self.root)["revision"]}
        runner.apply_result(self.state, "astra_review", decision, record, self.root, self.run)
        runner.lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the request
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(["Missing authorization check"],
                         [row["finding"] for row in findings.open_entries(self.state, "astra")])

    def validated(self, task_id):
        """A passing Validator validation of the current source, as the completion test builds it."""
        self.state["current_task"] = {**self.state["current_task"], "id": task_id}
        evidence = self.run / "sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": 0, "aggregated_output": "PASS"}}))
        current = support.snapshot(self.root)
        validation = {**{"contract_revision": self.state["goal_contract"]["revision"],
                         "contract_hash": self.state["goal_contract"]["hash"], "task_id": task_id,
                         "deferred_backlog": [],
                         "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                          "options": [], "proposed_delta": ""}},
                      "verdict": "PASS", "findings": [], "finding_dispositions": [], "unverified_criteria": [],
                      "checks_run": ["python3 -m unittest"],
                      "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
                      "criterion_results": [{"id": c["id"], "status": "PASS", "evidence_refs": ["event:check"]}
                                            for c in self.state["acceptance_criteria"]],
                      "end_to_end_result": {"status": "PASS", "summary": "Both CLI flows checked", "evidence_refs": ["event:check"]}}
        runner.apply_result(self.state, "sol", validation,
                            {"events": str(evidence), "source_revision": current["revision"], "output": str(evidence)},
                            self.root, self.run)
        return current

    def test_open_validator_findings_send_completion_back_to_the_validator_once(self):
        # Live greenfield and architecture runs (2026-09-29): the Validator's closing report was repaired, a
        # repair cannot close findings, and every COMPLETE was rejected until the run stopped.
        # An earlier Validator report raised the finding; its latest one passes without reporting it again.
        findings.record_validation(self.state, {"findings": [{"severity": "high", "finding": "AC7 wording is wrong",
                                                              "evidence": "event:check", "blocking": True}],
                                                "finding_dispositions": []}, {"output": "sol-01.json"})
        current = self.validated("task-recheck")
        self.assertEqual(["sol"], [row["source"] for row in findings.blocking_entries(self.state)])
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_review", self.astra_decision("COMPLETE"),
                                {"output": "complete-01.json", "source_revision": current["revision"]},
                                self.root, self.run)
        self.assertEqual(("RUNNING", "sol"), (self.state["status"], self.state["next_stage"]))
        # If the Validator leaves them open again, the gate refuses completion as before.
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(self.state, "astra_review", self.astra_decision("COMPLETE"),
                                {"output": "complete-02.json", "source_revision": current["revision"]},
                                self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)

    def test_a_completion_owner_finding_still_blocks_without_a_recheck(self):
        current = self.validated("task-own")
        findings.record_decision(self.state, self.astra_decision("REWORK", "Missing authorization check"),
                                 {"output": "review-02.json"})
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(self.state, "astra_review", self.astra_decision("COMPLETE"),
                                {"output": "complete-01.json", "source_revision": current["revision"]},
                                self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)

    def test_completion_rejects_a_blocking_finding_in_the_decision_and_the_ledger(self):
        self.state["current_task"] = {**self.state["current_task"], "id": "task-complete"}
        # A passing Validator validation exists, so only the findings stand between the run and completion.
        evidence = self.run / "sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": 0, "aggregated_output": "PASS"}}))
        current = support.snapshot(self.root)
        validation = {**{"contract_revision": self.state["goal_contract"]["revision"],
                         "contract_hash": self.state["goal_contract"]["hash"], "task_id": "task-complete",
                         "deferred_backlog": [],
                         "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                          "options": [], "proposed_delta": ""}},
                      "verdict": "PASS", "findings": [], "finding_dispositions": [], "unverified_criteria": [],
                      "checks_run": ["python3 -m unittest"],
                      "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": "event:check"}],
                      "criterion_results": [{"id": c["id"], "status": "PASS", "evidence_refs": ["event:check"]}
                                            for c in self.state["acceptance_criteria"]],
                      "end_to_end_result": {"status": "PASS", "summary": "Both CLI flows checked", "evidence_refs": ["event:check"]}}
        runner.apply_result(self.state, "sol", validation,
                            {"events": str(evidence), "source_revision": current["revision"], "output": str(evidence)},
                            self.root, self.run)
        # COMPLETE with a blocking finding in the decision itself is rejected.
        contradictory = self.astra_decision("COMPLETE", "Missing authorization check")
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(self.state, "astra_review", contradictory, {"output": "complete-01.json"}, self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        # And an open blocking ledger finding blocks completion even when the decision omits it.
        findings.record_decision(self.state, self.astra_decision("REWORK", "Missing authorization check"),
                                 {"output": "review-02.json"})
        clean = self.astra_decision("COMPLETE")
        with self.assertRaises(support.Paused) as caught:
            runner.apply_result(self.state, "astra_review", clean, {"output": "complete-02.json"}, self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_GATE", caught.exception.status)
        self.assertNotEqual("TASK_COMPLETE", self.state["status"])
        # Explicitly resolving it unblocks completion.
        fid = findings.open_entries(self.state, "astra")[0]["id"]
        clean = self.astra_decision("COMPLETE", dispositions=[dict(id=fid, disposition="resolved", evidence="event:check")])
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_review", clean, {"output": "complete-03.json"}, self.root, self.run)
        self.assertEqual("TASK_COMPLETE", self.state["status"])


if __name__ == "__main__":
    unittest.main()
