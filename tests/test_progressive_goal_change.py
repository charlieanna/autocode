"""Selective retirement through public report application and ordinary approval.

These are offline application-boundary tests, not a claim that the CLI gate passes.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_progressive_artifacts as artifacts
import autocode_progressive_completion as completion
import autocode_progressive_plan as rules
import autocode_progressive_state as progressive
import autocode_resolver_human as human
import autocode_util as util
import goal_fixtures


COMMAND = "python3 -m unittest test_greeting.py"


class GoalChangeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        goal_fixtures.seed_greeting_workspace(self.workspace)
        # The legacy worker guard also matches relative run paths across processes.
        self.run_dir = self.workspace / ".autocode" / "runs" / f"goal-change-{self.workspace.name}"
        self.run_dir.mkdir(parents=True)
        self.state = {"version": 3, "task_id": "goal-change", "task": "Greeting and rejection",
            "workspace": str(self.workspace), "run_dir": str(self.run_dir), "iteration": 0,
            "answers": {}, "user_events": [], "acceptance_criteria": [], "status": "RUNNING",
            "next_stage": "astra_discovery", "settings": {"joint_planning": True,
                "roles": {"glm": {"model": "fake-planner"}, "astra": {"model": "fake-reviewer"}},
                "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
                "limits": {"max_seconds": 43200}}}
        self.body = goal_fixtures.body()
        self.body["required_behaviors"] = ["Print the greeting", "Reject invalid input"]
        self.body["milestones"][0]["affected_paths"] = ["greet.py", "test_greeting.py"]
        self.first = {"objective": "Deliver greeting", "affected_paths": ["greet.py", "test_greeting.py"],
            "kind": "implement", "milestone_id": "M1", "requirements": ["Print greeting"],
            "acceptance_criteria": ["C1"], "validation_plan": [COMMAND]}
        def slice_row(sid, cid, tentative):
            return {"id": sid, "intended_result": "Deliver " + sid, "criterion_ids": ["C1"],
                "paths": ["greet.py", "test_greeting.py"], "depends_on": [], "tentative": tentative,
                "checks": [{"id": cid, "method": COMMAND,
                            "relation": "contributes_to", "criterion_ids": ["C1"]}]}
        self.proposal = {"version": 1, "needed_because": "Two independently useful product capabilities",
            "shared_decisions": ["Local CLI"], "outstanding_criteria": [], "done_slices": [],
            "slices": [slice_row("S1", "A", False), slice_row("S2", "B", True)]}

    def stage(self, stage, report):
        self.state["next_stage"] = stage
        output = self.run_dir / (stage + "-" + str(len(self.state.get("stages", []))) + ".json")
        output.write_text(json.dumps(report))
        snapshot = util.snapshot(self.workspace)
        record = {"stage": stage, "role": "glm" if stage in ("astra_discovery", "glm_revise") else "astra",
            "output": str(output), "iteration": 0, "exit_code": 0, "duration_seconds": 10,
            "source_revision": snapshot["revision"], "changed_files": []}
        if stage == "sol":
            record["role"] = "sol"
            result = subprocess.run(COMMAND, shell=True, cwd=self.workspace, capture_output=True, text=True, check=True)
            events = output.with_suffix(".jsonl")
            events.write_text(json.dumps({"type": "item.completed", "item": {"id": "check",
                "type": "command_execution", "command": COMMAND, "exit_code": result.returncode,
                "aggregated_output": result.stdout + result.stderr}}) + "\n")
            record["events"] = str(events)
        if stage in ("astra_challenge", "astra_finalize"):
            try:
                runner.planning.charge(self.state, stage, record=record)
            except util.Paused as error:
                self.assertEqual("PAUSED_PLANNING_BUDGET", error.status)
                # This fixture drives public admission/application itself, so
                # retain the admission pause before the actual CLI action.
                self.state.update(status=error.status, phase="PAUSED_OR_BLOCKED", stop_reason=str(error))
                self.authorize_review_limit()
                snapshot = util.snapshot(self.workspace)
                record["source_revision"] = snapshot["revision"]
                runner.planning.charge(self.state, stage, record=record)
        if progressive.view(self.state).get("budget"):
            if self.state.get("current_task"):
                record["task_id"] = self.state["current_task"]["id"]
            progressive.admit_attempt(self.state, record, snapshot)
            self.state["active_stage"] = record
        runner.account_stage(self.state, record)
        runner.apply_result(self.state, stage, copy.deepcopy(report), record, self.workspace, self.run_dir)

    def plan(self):
        common = {"summary": "Reviewed product slices", "contract_changes": [], "conflict_resolutions": [],
                  "requirement_trace": [], "progressive_proposal": copy.deepcopy(self.proposal)}
        self.stage("astra_discovery", {**common, "contract": copy.deepcopy(self.body),
            "code_refs": ["greet.py"], "alternatives": [], "uncertainties": []})
        self.stage("astra_challenge", {"summary": "No concerns", "concerns": []})
        self.stage("glm_revise", {**common, "contract": copy.deepcopy(self.body),
                                  "code_refs": ["greet.py"], "responses": []})
        self.stage("astra_finalize", {**common, "contract": {**copy.deepcopy(self.body),
                    "initial_task": copy.deepcopy(self.first)}, "decisions": []})
        human.evaluate(self.state)
        lifecycle.present(self.state)

    def validate(self, full=False):
        self.stage("sol", {**goal_fixtures.envelope(self.state), "verdict": "PASS", "findings": [],
            "checks_run": [COMMAND], "unverified_criteria": [] if full else ["C1"],
            "checks": [{"command": COMMAND, "exit_code": 0, "evidence_ref": "event:check"}],
            "end_to_end_result": {"status": "PASS" if full else "NOT_VERIFIED", "summary": "Real CLI check",
                                  "evidence_refs": ["event:check"] if full else []},
            "criterion_results": [{"id": "C1", "status": "PASS" if full else "NOT_VERIFIED",
                                   "evidence_refs": ["event:check"] if full else []}], "finding_dispositions": []})

    def checkpoint(self):
        self.stage("astra_review", {**goal_fixtures.envelope(self.state), "status": "CONTINUE",
            "progressive_checkpoint": True, "acceptance_criteria": copy.deepcopy(self.state["acceptance_criteria"]),
            "next_objective": "Next slice", "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                "acceptance_criteria": [], "validation_plan": [], "findings": []},
            "findings": [], "finding_dispositions": [], "agreed_limitations": [], "evidence": [], "blocker": "",
            "plan": ["Continue"], "affected_paths": []})

    def after_s1(self):
        self.plan()
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.validate()
        self.checkpoint()
        detailed = copy.deepcopy(self.proposal)
        detailed.update(done_slices=["S1"], slices=[copy.deepcopy(detailed["slices"][1])])
        detailed["slices"][0]["tentative"] = False
        self.stage("glm_revise", {"summary": "S2 on retained source", "progressive_proposal": detailed,
            "initial_task": copy.deepcopy(self.first)})
        self.stage("astra_finalize", {"summary": "Independent S2 review", "accepted": True,
            "product_changes": False, "permission_changes": False, "unresolved_product_decisions": False})
        self.old_token = goals.token(self.state["goal_contract"])
        self.old_check = copy.deepcopy(next(row for row in self.state["progressive"]["required_checks"] if row["id"] == "B"))
        self.old_history = copy.deepcopy(self.state["progressive"]["history"])
    def authorize_review_limit(self):
        # Only an actual isolated CLI operator action raises the exhausted
        # inherited pool ceiling; it never clears spent review/time receipts.
        path = self.run_dir / "state.json"
        path.write_text(json.dumps(self.state))
        result = subprocess.run([sys.executable, runner.__file__, "--workspace", str(self.workspace),
            "--run-dir", str(self.run_dir), "--planning-review-call-limit", "4"],
            env={**os.environ, "AUTOCODE_HOME": str(self.workspace / ".autocode" / "registry")},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.state = json.loads(path.read_text())
        pool = self.state["progressive"]["budget"]["pools"][self.state["progressive"]["active_allowance"]["pool_id"]]
        self.assertEqual((2, 4), (pool["reviews_used"], pool["review_limit"]))
        self.assertTrue(any(row["kind"] == "planning_budget_change" and row["actor"] == "user_cli"
                            for row in self.state["user_events"]))

    def revise(self, marker=True):
        self.body["required_behaviors"] = ["Print the greeting"]
        self.body["intended_outcome"] = "Greeting without the invalid-input requirement"
        line = marker if isinstance(marker, str) else progressive.retirement_line(self.old_check, "Reject invalid input")
        self.body["scope_exclusions"] = [line] if marker else []
        lifecycle.install_draft(self.state, copy.deepcopy(self.body), origin="user_cli_edit")
        self.proposal["slices"][0].update(id="S3")
        self.proposal["slices"][0]["checks"][0]["id"] = "N"
        self.proposal["slices"][1].update(id="S4")
        self.proposal["slices"][1]["checks"][0].update(id="F", relation="fully_verify")
        self.plan()

    def test_actual_review_reapproval_retirement_restart_and_fresh_full_proof(self):
        self.after_s1()
        self.revise()
        ledger = self.state["progressive"]
        self.assertEqual({"A", "B"}, {row["id"] for row in ledger["required_checks"]})
        self.assertFalse(ledger.get("retirements"))
        with self.assertRaises(util.Paused):
            progressive.guard_dispatch(self.state, "terra")
        spent = copy.deepcopy(ledger["budget"])
        tasks = copy.deepcopy(ledger["tasks"])
        attempts = copy.deepcopy(ledger["attempts"])
        selected = goals.token(self.state["goal_contract"])
        lifecycle.approve(self.state, selected)
        ledger = self.state["progressive"]
        self.assertEqual(spent, ledger["budget"])
        self.assertEqual(tasks, {key: ledger["tasks"][key] for key in tasks})
        self.assertEqual(attempts, ledger["attempts"])
        self.assertEqual(self.old_history, ledger["history"])
        self.assertNotEqual(self.old_token, selected)
        self.assertEqual({"A", "N"}, {row["id"] for row in ledger["required_checks"]})
        grant = ledger["retirements"][0]
        self.assertIn(grant["visible_removal"], self.state["goal_contract"]["body"]["constraints"])
        self.assertIn(grant["visible_removal"], self.state["planning"]["reports"]["astra_finalize"]["report"]["contract"]["constraints"])
        self.assertEqual(("B", rules.check_identity(self.old_check), selected),
                         (grant["check_id"], grant["check_hash"], grant["contract_token"]))
        report = artifacts.verify(self.run_dir, grant["artifact"])["report"]
        self.assertEqual(self.state["goal_contract"]["body"], report["contract_body"])
        self.assertEqual([self.old_check], report["retired_checks"])
        self.assertEqual(self.old_token, report["predecessor_contract_token"])
        self.assertFalse(completion.ready(self.state, util.snapshot(self.workspace)))
        self.state = json.loads(json.dumps(self.state))
        self.assertEqual("S3", progressive.require_active(self.state)["definition"]["id"])
        self.validate()
        self.checkpoint()
        detailed = copy.deepcopy(self.proposal)
        detailed.update(done_slices=["S1", "S3"], slices=[copy.deepcopy(detailed["slices"][1])])
        detailed["slices"][0]["tentative"] = False
        self.stage("glm_revise", {"summary": "Final slice", "progressive_proposal": detailed,
            "initial_task": copy.deepcopy(self.first)})
        self.stage("astra_finalize", {"summary": "Final independent review", "accepted": True,
            "product_changes": False, "permission_changes": False, "unresolved_product_decisions": False})
        self.validate(full=True)
        self.checkpoint()
        self.assertTrue(completion.ready(self.state, util.snapshot(self.workspace)))
        self.assertEqual(selected, self.state["progressive"]["completion_proof"]["contract_token"])
        self.assertGreater(self.state["progressive"]["budget"]["run_seconds"], spent["run_seconds"])

    def test_absent_marker_retains_removed_behavior_check(self):
        self.after_s1()
        self.revise(marker=False)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertIn("B", {row["id"] for row in self.state["progressive"]["required_checks"]})
        self.assertFalse(self.state["progressive"].get("retirements"))

    def test_stale_or_forged_declaration_rejects_without_partial_approval(self):
        self.after_s1()
        original = copy.deepcopy(self.state)
        original_body = copy.deepcopy(self.body)
        original_proposal = copy.deepcopy(self.proposal)
        for change in ("hash", "behavior", "unknown", "noncanonical"):
            with self.subTest(change=change):
                self.state = copy.deepcopy(original)
                self.body = copy.deepcopy(original_body)
                self.proposal = copy.deepcopy(original_proposal)
                check = copy.deepcopy(self.old_check)
                removes = "Reject invalid input"
                if change == "hash":
                    check["method"] = "python3 -m unittest unrelated"
                elif change == "unknown":
                    check["id"] = "unknown"
                elif change == "behavior":
                    removes = "Print the greeting"
                line = progressive.retirement_line(check, removes)
                self.revise(marker=line + " " if change == "noncanonical" else line)
                selected = goals.token(self.state["goal_contract"])
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "invalid explicit progressive check retirement"):
                    lifecycle.approve(self.state, selected)
                self.assertEqual(before, self.state)

    def test_old_token_or_preflight_alone_cannot_publish_retirements(self):
        self.after_s1()
        self.revise()
        selected = goals.token(self.state["goal_contract"])
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, self.old_token)
        self.assertEqual(before, self.state)
        prepared = progressive.prepare_seal(self.state, selected)
        with self.assertRaisesRegex(ValueError, "actual current user-approved"):
            progressive.seal(self.state, selected, prepared=prepared)
        self.assertEqual(before, self.state)

    def test_assignment_rejection_cannot_publish_approval_or_retirement(self):
        self.after_s1()
        self.first["affected_paths"] = ["outside.py"]
        self.revise()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "exceeds active slice"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)

    def test_conflicting_visible_retirement_cannot_reach_approval(self):
        self.after_s1()
        self.body["constraints"].append(progressive.retirement_line(self.old_check, "Print the greeting"))
        with self.assertRaisesRegex(ValueError, "differs from its explicit scope exclusion"):
            self.revise()
        self.assertFalse(goals.approved(self.state))
        self.assertFalse(self.state["progressive"].get("retirements"))
        self.assertIn("B", {row["id"] for row in self.state["progressive"]["required_checks"]})

    def test_removed_criterion_id_alone_cannot_retire_obligations(self):
        self.after_s1()
        self.body["acceptance_criteria"][0]["id"] = "C2"
        self.body["milestones"][0]["acceptance_criteria"] = ["C2"]
        self.first["acceptance_criteria"] = ["C2"]
        for row in self.proposal["slices"]:
            row["criterion_ids"] = ["C2"]
            for check in row["checks"]:
                check["criterion_ids"] = ["C2"]
                check["id"] += "-new"
        lifecycle.install_draft(self.state, copy.deepcopy(self.body), origin="user_cli_edit")
        self.plan()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "reconcile retained criterion/check"):
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
