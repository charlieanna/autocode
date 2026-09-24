"""Controller-level findings wiring: the actual apply_result branches, not just the helpers."""
import copy
import json
import unittest
from unittest.mock import patch

from . import test_autocode as base
from .goal_fixtures import approve_fixture

runner, support = base.runner, base.s
import autocode_findings as findings


class ControllerFindingsTests(unittest.TestCase):
    def setUp(self):
        base.RetrofitTest.setUp(self)
        approve_fixture(self.state, runner.goals)
        # A review needs an assigned task: approve the brief and take its first task.
        first = self.astra_decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        runner.goals.assign_task(self.state, first, support.snapshot(self.root))

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

    def test_completion_rejects_a_blocking_finding_in_the_decision_and_the_ledger(self):
        self.state["current_task"] = {**self.state["current_task"], "id": "task-complete"}
        # A passing Sol validation exists, so only the findings stand between the run and completion.
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
