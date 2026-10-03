"""Cumulative slice checks do not become whole-product proof or lose earlier obligations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import autocode_check_replay as replay
import autocode_progressive_state as progressive_state
import autocode_stage_context as stage_context
import autocode_test_cases as cases
import autocode_verification_plan as plan
from units import autoreview


def context():
    a = {"id": "A", "method": "python3 check_a.py", "relation": "contributes_to",
         "criterion_ids": ["C1"], "origin": "S1"}
    b = {"id": "B", "method": "python3 check_b.py", "relation": "fully_verify",
         "criterion_ids": ["C2"], "origin": "S2"}
    return {"active_slice": {"id": "S2", "intended_result": "B works", "paths": ["b.py"],
                             "criterion_ids": ["C2"], "checks": [b]},
            "required_checks": [a, b], "outstanding_criteria": ["C1"], "plan_hash": "approved-plan"}


def state_for(workspace):
    return {"version": 2, "task": "A and B", "workspace": str(workspace),
            "settings": {"roles": {role: {"model": "fake", "engine": "codex"}
                                    for role in ("astra", "terra", "sol")}},
            "goal_contract": {"body": {"acceptance_criteria": [
                {"id": "C1", "verification_method": "test: test_c1_complete"},
                {"id": "C2", "verification_method": "test: test_c2_complete"}]}},
            "current_task": {"acceptance_criteria": ["C2"], "validation_plan": ["python3 task_check.py"]}}


class ProgressiveVerificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.state = state_for(self.workspace)
        snapshot = mock.patch.object(stage_context.support, "snapshot", return_value={"revision": "source", "head": "head"})
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def test_ordinary_and_unapproved_proposals_have_no_progressive_context(self):
        for extra in ({}, {"progressive": {"candidate": {"proposal": context()}}}):
            state = {**self.state, **extra}
            self.assertEqual({}, progressive_state.context(state))
            self.assertIsNone(cases.in_scope(state))
            self.assertEqual(["python3 task_check.py"], autoreview.verification_commands(state))
            for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
                prompt, _ = stage_context.context_packet(state, stage, self.workspace / "state.json")
                self.assertNotIn("PROGRESSIVE VERIFICATION:", prompt)
                self.assertNotIn("progressive_verification", prompt)

    def test_last_slice_context_and_commands_include_earlier_contributions(self):
        expected = ["python3 task_check.py", "python3 check_a.py", "python3 check_b.py"]
        with mock.patch.object(progressive_state, "context", return_value=context()):
            self.assertEqual(expected, autoreview.verification_commands(self.state))
            for stage in ("terra", "sol", "astra_review", "astra_checkpoint"):
                prompt, metrics = stage_context.context_packet(self.state, stage, self.workspace / "state.json")
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(expected, packet["progressive_verification"]["required_commands"])
                self.assertEqual(context()["required_checks"], packet["progressive_verification"]["required_checks"])
                self.assertIn("only a contribution, never full proof", prompt)
                self.assertIn("Never relabel a real finding", prompt)
                self.assertEqual((len(prompt.encode()) + 3) // 4, metrics["estimated_prompt_tokens"])

    def test_expected_deferred_product_test_is_not_due_but_contribution_command_is(self):
        self.state["findings_ledger"] = {"real-defect": {"severity": "BLOCKER"}}
        before = copy.deepcopy(self.state)
        with mock.patch.object(progressive_state, "context", return_value=context()):
            self.assertEqual({"C2"}, cases.in_scope(self.state))
            self.assertEqual(["C2"], [case["id"] for case in cases.contract_cases(self.state)])
            self.assertIn("python3 check_a.py", autoreview.verification_commands(self.state))
        self.assertEqual(before, self.state, "scope selection must not modify findings or proof")

    def test_required_prose_and_empty_checklist_are_rejected_not_silently_omitted(self):
        for method in ("Inspect A and B", "test: test_c1_complete",
                       "python3 check_a.py passes; Validator reads the diff"):
            cumulative = context()
            cumulative["required_checks"][0]["method"] = method
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "no executable command"):
                plan.approved_commands(self.state, progressive_context=cumulative)
        with self.assertRaisesRegex(ValueError, "nonempty"):
            plan.approved_commands(self.state, progressive_context={**context(), "required_checks": []})

    def test_a_due_full_proof_cannot_be_hidden_by_outstanding_status(self):
        cumulative = context()
        cumulative["outstanding_criteria"].append("C2")
        with mock.patch.object(progressive_state, "context", return_value=cumulative):
            self.assertEqual({"C2"}, cases.in_scope(self.state))

    def test_full_targets_preserve_original_product_commands_not_last_slice_scope(self):
        criteria = self.state["goal_contract"]["body"]["acceptance_criteria"]
        criteria[0]["verification_method"] = "python3 future_product.py"
        criteria[1]["verification_method"] = "python3 product_journey.py"
        self.state["current_task"]["acceptance_criteria"] = ["C1"]
        commands = plan.approved_commands(self.state, progressive_context=context())
        self.assertIn("python3 product_journey.py", commands)
        self.assertNotIn("python3 future_product.py", commands)
        obligations = plan.product_checks(self.state["goal_contract"]["body"], context()["required_checks"])
        self.assertEqual(["C2"], [criterion for row in obligations for criterion in row["criterion_ids"]])
        self.assertEqual("fully_verify", obligations[0]["relation"])
        self.assertEqual(obligations, plan.product_checks(self.state["goal_contract"]["body"],
                                                        context()["required_checks"]))

    def test_local_pass_cannot_hide_failed_prescribed_product_journey(self):
        self.state["goal_contract"]["body"]["acceptance_criteria"][1]["verification_method"] = "python3 product_journey.py"
        calls = []
        def scratch_run(workspace, out, *, command, timeout):
            calls.append(command)
            return {"exit_code": 1 if command == "python3 product_journey.py" else 0, "tail": "fixture"}
        with self.assertRaisesRegex(ValueError, "product_journey.py.*exited 1"):
            replay.replay([{"command": "python3 check_b.py", "exit_code": 0, "evidence_ref": "event:b"}],
                          self.workspace, self.workspace / "run", {"output": "sol.json"}, scratch_run,
                          approved_state=self.state, progressive_context=context())
        self.assertIn("python3 product_journey.py", calls)

    def test_fabricated_pass_for_b_cannot_hide_broken_a_in_independent_replay(self):
        (self.workspace / "a.txt").write_text("broken A")
        (self.workspace / "b.txt").write_text("working B")
        calls = []
        def scratch_run(workspace, out, *, command, timeout):
            calls.append(command)
            failed = command == "python3 check_a.py" and (Path(workspace) / "a.txt").read_text() != "working A"
            return {"exit_code": 1 if failed else 0, "tail": "A regressed" if failed else "OK"}
        report = {"verdict": "PASS", "checks": [
            {"command": "python3 check_b.py", "exit_code": 0, "evidence_ref": "event:fabricated"}]}
        original = copy.deepcopy(report)
        with self.assertRaisesRegex(ValueError, "check_a.py.*exited 1"):
            replay.replay(report["checks"], self.workspace, self.workspace / "run",
                          {"output": "sol.json", "source_revision": "current-source"}, scratch_run,
                          approved_state=self.state, progressive_context=context())
        self.assertEqual(original, report, "rejection must not rewrite the claimed PASS into an accepted FAIL")
        self.assertEqual(["python3 check_b.py", "python3 task_check.py", "python3 check_a.py"], calls)
        receipt = json.loads((self.workspace / "run/check-replay/sol/replay.json").read_text())
        self.assertEqual("FAIL", receipt["verdict"])
        self.assertEqual("current-source", receipt["source_revision"])
        self.assertEqual(1, next(row["exit_code"] for row in receipt["checks"] if "check_a" in row["command"]))

    def test_explicit_required_commands_cannot_be_replaced_or_be_prose(self):
        calls = []
        def scratch_run(workspace, out, *, command, timeout):
            calls.append(command)
            return {"exit_code": 0}
        result = replay.replay([], self.workspace, self.workspace / "run", {}, scratch_run,
                               required_commands=["python3 check_a.py", "python3 check_b.py"])
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(["python3 check_a.py", "python3 check_b.py"], calls)
        with self.assertRaisesRegex(ValueError, "not an explicit executable command"):
            replay.replay([], self.workspace, self.workspace / "run", {}, scratch_run,
                          required_commands=["Inspect B"])
