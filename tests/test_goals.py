"""Goal gates in isolated Git workspaces. No real model or live run is used."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_stage_context as stage_context
import autocode_completion as completion_gate
import autocode_interventions as interventions
import autocode_support as s
import autocode_goals as g
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import assert_operational_wait, body, envelope, seed_greeting_workspace


class GoalTests(unittest.TestCase):
    def test_model_schema_requires_ownership_without_invalidating_legacy_contracts(self):
        before = copy.deepcopy(g.DISCOVERY_SCHEMA)
        schema = s.model_output_schema(g.DISCOVERY_SCHEMA)
        milestone = schema['properties']['contract']['properties']['milestones']['items']
        self.assertEqual(set(milestone['properties']), set(milestone['required']))
        self.assertIn('affected_paths', milestone['required'])
        self.assertIn('depends_on', milestone['required'])
        self.assertEqual(before, g.DISCOVERY_SCHEMA)
        legacy = body()
        for row in legacy['milestones']:
            row.pop('affected_paths', None)
            row.pop('depends_on', None)
        s.validate_schema({'contract': legacy, 'summary': 'Existing contract'}, g.DISCOVERY_SCHEMA)
        with self.assertRaisesRegex(ValueError, 'missing'):
            s.validate_schema({'contract': legacy, 'summary': 'New response'}, schema)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry_environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        self.registry_environment.start()
        self.addCleanup(self.registry_environment.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        seed_greeting_workspace(self.root)
        subprocess.run(["git", "-C", str(self.root), "add", "greet.py", "test_greeting.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        # The fixture's AutoCode registry lives in the temporary directory but is not
        # project source; keep it out of source snapshots (and resolver bindings).
        with (self.root / ".git/info/exclude").open("a") as exclude:
            exclude.write("/registry-home/\n")
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {"version": 2, "workspace": str(self.root), "task": "Make a greeting tool", "status": "RUNNING",
            "iteration": 1, "sessions": {}, "stages": [], "history": [], "next_stage": "terra", "acceptance_criteria": [],
            "settings": {"roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": self.local, "headroom": {"enabled": False}, "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3}}}
        lifecycle.migrate(self.state)

    def draft(self, **kwargs):
        lifecycle.install_draft(self.state, body(**kwargs), origin="test")

    def approve(self, **kwargs):
        self.draft(**kwargs)
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, g.token(self.state["goal_contract"]))

    def publish_review(self, criteria=("C1",)):
        """Publish the artifact review the way the Completion Owner requests it."""
        request = {"kind": "human_review", "criteria": list(criteria),
                   "decision_needed": "Review " + ", ".join(criteria) + " on the current artifact.",
                   "impact": "Human acceptance is required", "options": ["Approve", "Reject"],
                   "proposed_delta": ""}
        lifecycle.wait_for_user(self.state, request)
        self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
        return request

    def decision(self, status="CONTINUE"):
        criteria = [{**c, "status": "verified", "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria, "next_objective": "Implement greeting",
                "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet valid names; reject empty names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Execute both CLI cases"]},
                "agreed_limitations": [],
                "blocker": "", "evidence": ["event:check"], "plan": ["Greeting and checks"], "affected_paths": ["greet.py"]}

    def validation(self, *, passed=True):
        outcome, exit_code = ("PASS", 0) if passed else ("FAIL", 1)
        evidence = self.run / "sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": exit_code, "aggregated_output": outcome}}))
        current = s.snapshot(self.root)
        value = {**envelope(self.state), "verdict": outcome, "findings": [], "unverified_criteria": [],
                 "checks_run": ["python3 -m unittest"], "checks": [{"command": "python3 -m unittest", "exit_code": exit_code,
                     "evidence_ref": "event:check"}], "criterion_results": [
                         {"id": c["id"], "status": outcome, "evidence_refs": ["event:check"]}
                         for c in self.state["acceptance_criteria"]]}
        value["end_to_end_result"] = {"status": outcome, "summary": "Both CLI flows checked", "evidence_refs": ["event:check"]}
        record = {"events": str(evidence), "source_revision": current["revision"], "output": str(evidence)}
        runner.apply_result(self.state, "sol", value, record, self.root, self.run)
        return current

    def invoke(self, *args, role=None):
        if ("--resolver-token" not in args and any(flag in args for flag in ("--answer", "--delegate"))
                and self.state.get("status") in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL", "RESOLVER_PENDING")):
            # Answer the way a user does: a plain run publishes the current request at
            # the runner's writer boundary and displays it, then the user passes the
            # token it displayed. Stale-token tests pass --resolver-token themselves.
            self.invoke("--no-chat")
            published = self.state.get("resolver_human_request") or {}
            if published.get("request_token"):
                args = (*args, "--resolver-token", published["request_token"])
        s.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]
        with patch.object(sys, "argv", argv), patch.object(s, "assert_no_legacy_process"), \
             patch.object(s, "local_settings", return_value=self.local), \
             patch.object(runner, "run_role", side_effect=role or AssertionError("No agent may launch")), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = runner.main()
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        self.state = s.read(self.run / "state.json")
        return code

    def test_vague_task_starts_read_only_discovery_and_waits(self):
        calls = []
        def interview(**kwargs):
            calls.append(kwargs)
            return {"contract": body(questions=True), "summary": "Need interface"}, {
                "output": str(self.run / "draft.json"), "duration_seconds": 0.01}
        self.assertEqual(2, self.invoke(role=interview))
        self.assertEqual(1, len(calls))
        self.assertEqual("astra_discovery", calls[0]["state"]["stages"][-1].get("stage", "astra_discovery"))
        self.assertFalse(calls[0]["allow_write"])
        self.assertEqual("read-only", calls[0]["sandbox"])
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertFalse(g.approved(self.state))

    def test_existing_answers_persist_and_cannot_be_asked_again(self):
        self.draft(questions=True)
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI"))
        self.assertEqual("CLI", self.state["answers"]["Q1"]["text"])
        prompt, _ = stage_context.context_packet(self.state, "astra_discovery", self.run / "state.json")
        self.assertIn('"Q1"', prompt)
        self.assertIn('"text": "CLI"', prompt)
        with self.assertRaisesRegex(ValueError, "already answered"):
            self.draft(questions=True)

    def test_permission_response_keeps_the_approved_goal_and_evidence(self):
        self.approve()
        contract = copy.deepcopy(self.state["goal_contract"])
        self.state["validation"] = {"verdict": "FAIL", "source_revision": "pinned"}
        self.state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                          user_request={"kind":"permission", "decision_needed":"Authorize bounded repair", "impact":"Limit reached", "options":[], "proposed_delta":"limit only"},
                          pending_questions=[{"id":"decision-limit", "question":"Authorize bounded repair", "why":"Limit reached", "options":[], "proposed_default":""}])
        g.resolve_permission(self.state, "decision-limit", "Approve the saved limit change")
        self.assertEqual(contract, self.state["goal_contract"])
        self.assertEqual({"verdict": "FAIL", "source_revision": "pinned"}, self.state["validation"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertNotIn("user_request", self.state)
        self.assertEqual("permission_answer", self.state["answers"]["decision-limit"]["kind"])

    def test_is_operational_response_classifies_permission_and_scoped_blocker_only(self):
        # kind="permission" is always operational, regardless of proposed_delta.
        self.assertTrue(g.is_operational_response({"kind": "permission", "proposed_delta": ""}))
        self.assertTrue(g.is_operational_response({"kind": "permission"}))
        # kind="blocker" is operational only with one of the two exact canonical
        # no-scope-change prefixes.
        self.assertTrue(g.is_operational_response({"kind": "blocker",
            "proposed_delta": "No goal, scope, criterion, or behavior change. Extend the tool timeout."}))
        self.assertTrue(g.is_operational_response({"kind": "blocker",
            "proposed_delta": "No contract, product, acceptance-criterion, implementation-scope, "
                               "filesystem, provider or spending change. Raise the milestone budget."}))
        # Negative controls: a criterion relaxation, scope change, permission
        # grant phrased as a blocker without the exact prefix, a stale/generic
        # blocker, and every other kind must all still require reapproval.
        self.assertFalse(g.is_operational_response({"kind": "blocker", "proposed_delta": "Extend the tool timeout"}))
        self.assertFalse(g.is_operational_response({"kind": "blocker", "proposed_delta": ""}))
        self.assertFalse(g.is_operational_response({"kind": "blocker"}))
        for kind in ("goal_change", "contradiction", "infeasible", "clarification", "none"):
            self.assertFalse(g.is_operational_response({"kind": kind, "proposed_delta": ""}))
        self.assertFalse(g.is_operational_response({}))
        self.assertFalse(g.is_operational_response(None))

    def test_astra_decisions_prompt_tells_the_model_when_to_use_permission_kind(self):
        # Without this guidance a model has no way to know the runtime treats
        # "permission" and "blocker" differently, so it can only reach the
        # non-invalidating route by chance (see issue #78). Guard the text
        # that actually gives it that instruction.
        self.assertIn('kind="permission"', s.ASTRA_DECISIONS)
        self.assertIn("never choose it merely", s.ASTRA_DECISIONS)

    def operational_limit_request(self):
        """The issue #78 scenario: a stopped operational exhaustion request presenting
        only the option to extend finite execution limits, with no product delta."""
        return {"kind": "permission", "decision_needed": "Extend milestone/tool limits to finish verification?",
                "impact": "Verification stopped at the current execution/tool budget",
                "options": ["Extend milestone/tool execution limits and finish verification",
                            "Accept the current failing verification"],
                "discovered": "The milestone active-time budget was exhausted mid-verification",
                "proposed_delta": ""}

    def test_cli_answer_to_an_operational_limit_request_preserves_approval_and_does_not_replan(self):
        self.approve()
        contract = copy.deepcopy(self.state["goal_contract"])
        self.state["validation"] = {"verdict": "FAIL", "source_revision": "pinned"}
        validation = copy.deepcopy(self.state["validation"])
        request = self.operational_limit_request()
        self.state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                          user_request=request,
                          pending_questions=[{"id": "decision-limit", "question": request["decision_needed"],
                                               "why": request["impact"], "options": request["options"],
                                               "proposed_default": ""}])
        self.assertEqual(0, self.invoke("--answer",
            "decision-limit=Extend milestone/tool execution limits and finish verification"))
        self.assertEqual(contract, self.state["goal_contract"])
        self.assertEqual(validation, self.state["validation"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertNotIn("user_request", self.state)
        self.assertEqual("permission_answer", self.state["answers"]["decision-limit"]["kind"])

    def test_chat_answer_to_an_operational_limit_request_preserves_approval_and_does_not_replan(self):
        self.approve()
        contract = copy.deepcopy(self.state["goal_contract"])
        self.state["validation"] = {"verdict": "FAIL", "source_revision": "pinned"}
        validation = copy.deepcopy(self.state["validation"])
        request = self.operational_limit_request()
        self.state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                          user_request=request,
                          pending_questions=[{"id": "decision-limit", "question": request["decision_needed"],
                                               "why": request["impact"], "options": request["options"],
                                               "proposed_default": ""}])
        with patch("builtins.input", side_effect=["Extend milestone/tool execution limits and finish verification"]):
            result = runner.chat_checkpoint(self.state, None)
        self.assertTrue(result)
        self.assertEqual(contract, self.state["goal_contract"])
        self.assertEqual(validation, self.state["validation"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertNotIn("user_request", self.state)
        self.assertEqual("permission_answer", self.state["answers"]["decision-limit"]["kind"])

    def test_chat_answer_to_a_genuine_blocker_still_requires_reapproval(self):
        # Negative control: an unrelated blocker/goal_change decision, or a
        # blocker whose own proposed_delta does not state the exact no-scope-change
        # prefix, must still invalidate approval and route back through discovery.
        for request in (
            {"kind": "blocker", "decision_needed": "Relax the timeout criterion?", "impact": "Tests are slow",
             "options": ["Relax it", "Keep it"], "discovered": "Slow CI", "proposed_delta": "Relax criterion C1"},
            {"kind": "goal_change", "decision_needed": "Add a new export format?", "impact": "New scope",
             "options": ["Add it", "Skip it"], "discovered": "User asked for CSV too", "proposed_delta": "Add CSV export"},
        ):
            with self.subTest(kind=request["kind"]):
                self.setUp()
                self.approve()
                question = {"id": "decision-1", "question": request["decision_needed"],
                            "why": request["impact"], "options": request["options"], "proposed_default": ""}
                if request["kind"] == "blocker":
                    # A genuine blocker reaches the user only after AutoResolver's read-only
                    # diagnosis could not resolve it; the chat reply is then information for
                    # AutoResolver, never an operational permission that keeps building.
                    diagnosis = self.run / "resolver-diagnosis.json"
                    s.atomic_json(diagnosis, {"diagnosis": "Relaxing C1 needs the user"})
                    self.state["stages"].append({"stage": "astra_resolve", "output": str(diagnosis),
                                                 "exit_code": 0, "changed_files": []})
                    lifecycle.human.queue(self.state, "blocker", {"stage": "astra_resolve", "output": str(diagnosis)},
                                  request=request, questions=[question], next_stage="astra_review",
                                  evidence={"diagnosis": "Relaxing C1 needs the user", "output": str(diagnosis)})
                    self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
                    with patch("builtins.input", side_effect=[request["options"][0]]), \
                            contextlib.redirect_stdout(io.StringIO()):
                        self.assertFalse(runner.chat_checkpoint(self.state, None))
                    self.assertTrue(self.state["status"].startswith("PAUSED_"))
                    self.assertNotIn("decision-1", self.state.get("answers", {}))
                    self.assertFalse([e for e in self.state.get("user_events", []) if e.get("kind") == "permission_answer"])
                    self.assertIsNone(lifecycle.human.current(self.state))
                    self.assertEqual(request["options"][0],
                                     self.state["recovery_context"]["human_information"]["text"])
                    self.assertEqual("hold", next(iter(
                        self.state["resolver"]["human_response_resolutions"].values()))["action"])
                    continue
                self.state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                                  user_request=request, pending_questions=[question])
                with patch("builtins.input", side_effect=[request["options"][0]]):
                    runner.chat_checkpoint(self.state, None)
                self.assertEqual("draft", self.state["goal_contract"]["approval_status"])
                self.assertEqual("astra_discovery", self.state["next_stage"])

    def permission_request(self):
        return {"kind": "permission", "decision_needed": "Repair the fallback test?",
                "impact": "The exact test is excluded", "options": ["Repair", "Keep excluded"],
                "discovered": "An assertion races navigation", "proposed_delta": "Only the fallback test"}

    def answer_permission(self, text="Repair only that test"):
        request = self.permission_request()
        lifecycle.wait_for_user(self.state, request)
        lifecycle.human.evaluate(self.state)
        qid = self.state["pending_questions"][0]["id"]
        g.resolve_permission(self.state, qid, text)
        return request, qid

    def test_exact_permission_reuses_real_answer_including_a_denial(self):
        for answer in ("Repair only that test", "No, leave it excluded"):
            with self.subTest(answer=answer):
                self.approve()
                request, qid = self.answer_permission(answer)
                original_contract = copy.deepcopy(self.state["goal_contract"])
                lifecycle.wait_for_user(self.state, copy.deepcopy(request))
                lifecycle.human.evaluate(self.state)
                self.assertEqual("RUNNING", self.state["status"])
                self.assertEqual("astra_review", self.state["next_stage"])
                self.assertEqual([], self.state["pending_questions"])
                self.assertEqual(answer, self.state["permission_reuse_context"]["answer"])
                self.assertEqual(qid, self.state["permission_reuse_context"]["answer_id"])
                self.assertEqual(original_contract, self.state["goal_contract"])
                with self.assertRaises(s.Paused) as caught:
                    lifecycle.wait_for_user(self.state, request)
                    lifecycle.human.evaluate(self.state)
                self.assertEqual("PAUSED_PERMISSION_RECONCILIATION", caught.exception.status)

    def test_permission_reuse_never_expands_scope_or_trusts_missing_provenance(self):
        for mode in ("wider_scope", "changed_contract", "forged_event", "legacy_answer"):
            with self.subTest(mode=mode):
                self.approve()
                request, qid = self.answer_permission()
                answer = self.state["answers"][qid]
                if mode == "wider_scope":
                    request["proposed_delta"] = "Change production navigation too"
                elif mode == "changed_contract":
                    answer["contract_token"] = "stale"
                elif mode == "forged_event":
                    self.state["user_events"].remove(answer)
                else:
                    answer.pop("request")
                lifecycle.wait_for_user(self.state, request)
                lifecycle.human.evaluate(self.state)
                self.assertEqual("WAITING_FOR_USER", self.state["status"])
                self.assertNotEqual(qid, self.state["pending_questions"][0]["id"])

    def test_timeout_requires_a_changed_plan_or_explicitly_changed_limits(self):
        self.approve()
        decision = self.decision()
        current = s.snapshot(self.root)
        lifecycle.assign_task(self.state, decision, current)
        self.state["settings"]["limits"].update(tool_timeout_seconds=1800)
        self.state["recovery_context"] = {
            "task_id": self.state["current_task"]["id"], "timeout_kind": "tool",
            "execution_limits": {"tool_timeout_seconds": 1800}}
        unchanged = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "changed execution plan"):
            lifecycle.assign_task(self.state, decision, current)
        self.assertEqual(unchanged, self.state)
        for paths in (["previous-milestone.py"], ["greet.py", "unowned.py"]):
            with self.subTest(paths=paths):
                self.state = copy.deepcopy(unchanged)
                revised = copy.deepcopy(decision)
                revised["affected_paths"] = paths
                with self.assertRaisesRegex(ValueError, "changed execution plan"):
                    lifecycle.assign_task(self.state, revised, current)
                self.assertEqual(unchanged, self.state)
        self.state = copy.deepcopy(unchanged)
        self.state["settings"]["limits"]["stage_timeout_seconds"] = 7200
        with self.assertRaisesRegex(ValueError, "changed execution plan"):
            lifecycle.assign_task(self.state, decision, current)
        self.state = copy.deepcopy(unchanged)
        revised = copy.deepcopy(decision)
        revised["next_task"]["validation_plan"] = ["Reuse the pinned CLI result; run remaining invalid-name check separately"]
        lifecycle.assign_task(self.state, revised, current)
        self.assertNotEqual(unchanged["current_task"]["id"], self.state["current_task"]["id"])
        self.state = unchanged
        self.state["settings"]["limits"]["tool_timeout_seconds"] = 3600
        lifecycle.assign_task(self.state, decision, current)

    def test_stalled_replan_requires_a_change_to_the_effective_assignment(self):
        import autocode_milestones as milestones
        self.approve()
        self.state["settings"]["milestone_checkpoints"] = {**milestones.DEFAULTS, "stalled_reviews": 1}
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        current = self.validation(passed=False)
        unchanged = copy.deepcopy(self.state)
        for paths in (["greet.py"], ["previous-milestone.py"], ["greet.py", "unowned.py"]):
            with self.subTest(paths=paths):
                self.state = copy.deepcopy(unchanged)
                decision = self.decision("REWORK")
                decision["affected_paths"] = paths
                with self.assertRaises(s.Paused) as caught:
                    lifecycle.assign_task(self.state, decision, current)
                self.assertEqual("PAUSED_MILESTONE_REPLAN", caught.exception.status)
                self.assertEqual(unchanged, self.state)
        self.state = copy.deepcopy(unchanged)
        decision = self.decision("REWORK")
        decision["next_task"]["validation_plan"] = ["Isolate the failing empty-input case before the full suite"]
        self.assertEqual("implement", lifecycle.assign_task(self.state, decision, current))

    def test_human_only_pending_review_can_be_presented_accepted_and_completed(self):
        import autocode_milestones as milestones
        draft = body(human=True)
        draft["acceptance_criteria"].append({"id": "C2", "criterion": "Automated checks pass",
            "verification_method": "Execute CLI cases", "human_review": False})
        draft["milestones"][0]["acceptance_criteria"].append("C2")
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        next_task = self.decision()
        next_task["next_task"]["acceptance_criteria"].append("C2")
        lifecycle.assign_task(self.state, next_task, s.snapshot(self.root))
        current = self.validation()
        val = self.state["validation"]
        val.update(verdict="BLOCKED", unverified_criteria=["C1 human acceptance pending"])
        val["criterion_results"][0]["status"] = "NOT_VERIFIED"
        decision = self.decision("TASK_COMPLETE")
        self.assertFalse(completion_gate.completion_ready(self.state, decision, current))
        self.assertFalse(milestones.evidence_ready(self.state, current))
        self.assertTrue(completion_gate.completion_ready(self.state, decision, current, require_human_reviews=False))
        runner.apply_result(self.state, "astra_review", decision, {"output": "review"}, self.root, self.run)
        lifecycle.human.evaluate(self.state)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("human_review", lifecycle.human.current(self.state)["scope"])
        lifecycle.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        self.assertTrue(completion_gate.completion_ready(self.state, decision, current))
        self.assertTrue(milestones.evidence_ready(self.state, current))
        val = self.state["validation"]
        val["criterion_results"][1]["status"] = "FAIL"
        self.assertFalse(completion_gate.completion_ready(self.state, decision, current))
        self.assertFalse(milestones.evidence_ready(self.state, current))
        val["criterion_results"][1]["status"] = "PASS"
        self.assertEqual("BLOCKED", val["verdict"], "Do not rewrite the independent report")
        runner.apply_result(self.state, "astra_review", decision, {"output": "complete"}, self.root, self.run)
        self.assertEqual("TASK_COMPLETE", self.state["status"])

    def test_multiple_human_criteria_can_each_be_reviewed_and_completed(self):
        """LIVE-02 regression: two human-review criteria must not deadlock acceptance."""
        import autocode_milestones as milestones
        draft = body(human=True)
        draft["acceptance_criteria"].append(
            {"id": "C2", "criterion": "Human README cross-check", "verification_method": "Read and compare",
             "human_review": True})
        draft["acceptance_criteria"].append({"id": "C3", "criterion": "Automated checks pass",
            "verification_method": "Execute CLI cases", "human_review": False})
        draft["milestones"][0]["acceptance_criteria"] += ["C2", "C3"]
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        next_task = self.decision()
        next_task["next_task"]["acceptance_criteria"] += ["C2", "C3"]
        lifecycle.assign_task(self.state, next_task, s.snapshot(self.root))
        current = self.validation()
        val = self.state["validation"]
        # The Validator's report: both human criteria pending, the technical one passes.
        by_id = {row["id"]: row for row in val["criterion_results"]}
        by_id["C1"]["status"] = "NOT_VERIFIED"
        by_id["C2"]["status"] = "NOT_VERIFIED"
        by_id["C3"]["status"] = "PASS"
        val.update(verdict="BLOCKED", unverified_criteria=["C1 human acceptance pending",
                                                           "C2 human acceptance pending"])
        decision = self.decision("TASK_COMPLETE")
        self.assertTrue(completion_gate.completion_ready(self.state, decision, current, require_human_reviews=False),
                        "a two-human contract reaches the artifact review like a one-human contract")
        runner.apply_result(self.state, "astra_review", decision, {"output": "review"}, self.root, self.run)
        lifecycle.human.evaluate(self.state)
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual("human_review", lifecycle.human.current(self.state)["scope"])
        lifecycle.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        self.assertFalse(completion_gate.completion_ready(self.state, decision, current),
                         "one of two human acceptances is still missing")
        g.approve_review(self.state, "C2", g.review_token(self.state), current)
        self.assertTrue(completion_gate.completion_ready(self.state, decision, current))
        runner.apply_result(self.state, "astra_review", decision, {"output": "complete"}, self.root, self.run)
        self.assertEqual("TASK_COMPLETE", self.state["status"])

    def test_task_ownership_uses_only_the_named_milestone_paths(self):
        """A completed milestone's reported paths do not grant the next task writes."""
        import autocode_milestones as milestones
        draft = body()
        draft["acceptance_criteria"].append({"id": "C2", "criterion": "Server behavior",
            "verification_method": "Execute handler checks", "human_review": False})
        draft["milestones"].append({"id": "M2", "objective": "server", "acceptance_criteria": ["C2"],
                                    "depends_on": ["M1"], "affected_paths": ["server/"]})
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        self.state["settings"]["milestone_checkpoints"] = copy.deepcopy(milestones.DEFAULTS)
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))  # M1 assigned
        self.validation()  # M1's independent validation passes
        stray = self.decision()
        stray["next_task"] = {"kind": "implement", "milestone_id": "M2", "requirements": ["server"],
                              "acceptance_criteria": ["C2"], "validation_plan": ["run"],
                              "findings": []}
        stray["affected_paths"] = ["greet.py"]  # M1's path, not M2's server/
        for paths in (["greet.py"], ["server/handler.py"], ["greet.py", "server/handler.py"]):
            with self.subTest(paths=paths):
                state = copy.deepcopy(self.state)
                stray["affected_paths"] = paths
                lifecycle.assign_task(state, stray, s.snapshot(self.root))
                task = state["current_task"]
                # Contract-legal new outputs stay allowed, but previous/other owners do not.
                self.assertEqual(["server/"], task["affected_paths"])
                self.assertEqual("M2", task["milestone_id"])
                self.assertEqual(stray["next_task"]["requirements"], task["requirements"])
                self.assertEqual(stray["next_task"]["validation_plan"], task["validation_plan"])
                prompt, _ = stage_context.context_packet(state, "terra", self.run / "state.json")
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(["server/"], packet["affected_paths"])
                # Read-only revalidation can still check already accepted M1 criteria.
                recheck = copy.deepcopy(stray)
                recheck["next_task"].update(kind="validate", acceptance_criteria=["C1", "C2"],
                    validation_plan=["Execute greeting CLI and server handler checks"])
                lifecycle.assign_task(state, recheck, s.snapshot(self.root))
                self.assertEqual(["C1", "C2"], state["current_task"]["acceptance_criteria"])
                self.assertEqual(recheck["next_task"]["validation_plan"], state["current_task"]["validation_plan"])
                self.assertEqual(["server/"], state["current_task"]["affected_paths"])

    def test_serial_rework_delivers_corrected_paths_to_builder(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        contract = copy.deepcopy(self.state['goal_contract'])
        repair = self.decision('REWORK')
        repair['affected_paths'] = ['greet.py', 'formatting.py', 'test_greeting.py']
        repair['next_task']['requirements'] = ['Correct formatting.py to meet the approved greeting criterion']
        lifecycle.assign_task(self.state, repair, s.snapshot(self.root))
        prompt, _ = stage_context.context_packet(self.state, 'terra', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(repair['affected_paths'], packet['current_task']['affected_paths'])
        self.assertEqual(repair['affected_paths'], packet['affected_paths'])
        self.assertEqual(repair['next_task']['requirements'], packet['current_task']['requirements'])
        self.assertEqual(contract, self.state['goal_contract'])
        # A later report naming only one file must keep the already admitted
        # repair scope, even when it repeats the same objective and recipe.
        repair['affected_paths'] = ['test_greeting.py']
        lifecycle.assign_task(self.state, repair, s.snapshot(self.root))
        prompt, _ = stage_context.context_packet(self.state, 'terra', self.run / 'state.json')
        repeated = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(packet['affected_paths'], repeated['affected_paths'])

    def test_serial_rework_retains_existing_paths_when_repair_names_a_subset(self):
        draft = body()
        draft['milestones'][0]['affected_paths'] = ['greet.py', 'test_greeting.py']
        lifecycle.install_draft(self.state, draft, origin='test')
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, g.token(self.state['goal_contract']))
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        owned = list(self.state['current_task']['affected_paths'])
        repair = self.decision('REWORK')
        repair['affected_paths'] = owned[:1]
        repair['next_task']['requirements'] = ['Correct the greeting behavior for the approved criterion']
        lifecycle.assign_task(self.state, repair, s.snapshot(self.root))
        prompt, _ = stage_context.context_packet(self.state, 'terra', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(owned, packet['current_task']['affected_paths'])
        self.assertEqual(owned, packet['affected_paths'])

    def test_worker_rework_does_not_expand_milestone_ownership(self):
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.state['parent_run'] = 'orchestrator'
        owned = self.state['current_task']['affected_paths']
        repair = self.decision('REWORK')
        repair['affected_paths'] = [*owned, 'another_worker.py']
        lifecycle.assign_task(self.state, repair, s.snapshot(self.root))
        prompt, _ = stage_context.context_packet(self.state, 'terra', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertEqual(owned, packet['current_task']['affected_paths'])

    def test_task_paths_fall_back_to_report_without_milestone_ownership(self):
        for ownership in (None, []):
            with self.subTest(ownership=ownership):
                draft = body()
                if ownership is None:
                    draft["milestones"][0].pop("affected_paths")
                else:
                    draft["milestones"][0]["affected_paths"] = ownership
                lifecycle.install_draft(self.state, draft, origin="test")
                lifecycle.human.evaluate(self.state)
                lifecycle.present(self.state)
                lifecycle.approve(self.state, self.state["displayed_goal"])
                decision = self.decision()
                decision["affected_paths"] = ["greet.py", "test_greeting.py"]
                lifecycle.assign_task(self.state, decision, s.snapshot(self.root))
                self.assertEqual(decision["affected_paths"], self.state["current_task"]["affected_paths"])

    def test_execution_handoff_scopes_design_and_highlights_saved_permission(self):
        self.approve()
        self.state["settings"]["figma_file"] = "https://www.figma.com/design/Example123/Task"
        request, qid = self.answer_permission()
        lifecycle.wait_for_user(self.state, request)
        lifecycle.human.evaluate(self.state)
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        prompt, _ = stage_context.context_packet(self.state, "terra", self.run / "state.json")
        self.assertIn('bounded test, parser, or harness repair', prompt)
        self.assertNotIn('before planning, implementation or validation', prompt)
        self.assertIn('"permission_reuse_context"', prompt)
        self.assertIn(qid, prompt)
        self.assertIn('Read saved_answers before raising', prompt)

    def test_human_acceptance_cannot_cover_invalid_technical_evidence(self):
        self.approve(human=True)
        current = self.validation()
        self.state["validation"].update(verdict="BLOCKED", unverified_criteria=["C1"])
        self.state["validation"]["criterion_results"][0]["status"] = "NOT_VERIFIED"
        original = copy.deepcopy(self.state)
        mutations = [
            lambda v: v.update(unverified_criteria=["C1", "another gap"]),
            lambda v: v["checks"][0].update(exit_code=1),
            lambda v: v.update(findings=[{"blocking": True, "severity": "medium"}]),
            lambda v: v.update(criteria_revision="stale"),
            lambda v: v.update(source_revision="stale"),
            lambda v: v["end_to_end_result"].update(status="NOT_VERIFIED"),
            lambda v: v["criterion_results"].append(copy.deepcopy(v["criterion_results"][0])),
            lambda v: v["criterion_results"][0].update(evidence_refs=[]),
        ]
        for mutate in mutations:
            self.state = copy.deepcopy(original)
            mutate(self.state["validation"])
            lifecycle.human.evaluate(self.state)
            lifecycle.present(self.state)
            with self.assertRaises(ValueError):
                g.approve_review(self.state, "C1", g.review_token(self.state), current)
            self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current,
                                                require_human_reviews=False))

    def test_answer_is_never_approval_and_resume_does_not_bypass_remaining_question(self):
        draft = body(questions=True)
        draft["open_blocking_questions"].append({**draft["open_blocking_questions"][0], "id": "Q2"})
        lifecycle.install_draft(self.state, draft, origin="test")
        self.invoke("--answer", "Q1=CLI")
        self.assertFalse(g.approved(self.state))
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.assertEqual(["Q2"], [q["id"] for q in self.state["pending_questions"]])

    def test_answer_with_resume_paused_dispatches_the_next_stage(self):
        """#509: --resume-paused after a gate-clearing answer continues in this invocation."""
        self.draft(questions=True)
        launched = []

        def halt(**kwargs):
            launched.append(kwargs)
            raise ValueError("halt: dispatch reached the build loop")

        self.assertEqual(2, self.invoke("--answer", "Q1=CLI", "--resume-paused", role=halt))
        self.assertEqual(1, len(launched), "the same invocation must dispatch the next stage")
        self.assertIn("Resumed: dispatching the next stage.", self.stdout)
        self.assertNotIn("Saved; no agent launched", self.stdout)
        self.assertEqual("halt: dispatch reached the build loop", self.state["stop_reason"])

    def test_answer_without_resume_still_saves_without_launching(self):
        self.draft(questions=True)
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI"))
        self.assertIn("Saved; no agent launched by this action.", self.stdout)
        self.assertEqual("RUNNING", self.state["status"])

    def test_resume_paused_with_a_remaining_question_still_saves_without_launching(self):
        draft = body(questions=True)
        draft["open_blocking_questions"].append({**draft["open_blocking_questions"][0], "id": "Q2"})
        lifecycle.install_draft(self.state, draft, origin="test")
        self.assertEqual(0, self.invoke("--answer", "Q1=CLI", "--resume-paused"))
        self.assertIn("Saved; no agent launched by this action.", self.stdout)
        self.assertEqual(["Q2"], [q["id"] for q in self.state["pending_questions"]])

    def test_approve_goal_with_resume_paused_dispatches_the_build(self):
        self.draft()
        selected = g.token(self.state["goal_contract"])
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        launched = []

        def halt(**kwargs):
            launched.append(kwargs)
            raise ValueError("halt: dispatch reached the build loop")

        self.assertEqual(2, self.invoke("--approve-goal", selected, "--resume-paused", role=halt))
        self.assertEqual(1, len(launched), "the same invocation must dispatch after the approval")
        self.assertIn("Resumed: dispatching the next stage.", self.stdout)
        self.assertTrue(g.approved(self.state))
        self.assertEqual("halt: dispatch reached the build loop", self.state["stop_reason"])

    def test_approval_requires_displayed_exact_revision(self):
        self.draft()
        selected = g.token(self.state["goal_contract"])
        with self.assertRaises(ValueError): lifecycle.approve(self.state, selected)
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, selected)
        self.assertTrue(g.approved(self.state))
        self.draft()
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        with self.assertRaises(ValueError): lifecycle.approve(self.state, selected)
        self.assertFalse(g.approved(self.state))

    def test_edit_invalidates_approval_validation_and_human_review(self):
        self.approve(human=True)
        current = self.validation()
        self.publish_review()
        lifecycle.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        old_token = g.token(self.state["goal_contract"])
        revised = body(human=True); revised["required_behaviors"].append("Support Unicode names")
        lifecycle.install_draft(self.state, revised, origin="user_edit")
        self.assertNotEqual(old_token, g.token(self.state["goal_contract"]))
        self.assertNotIn("validation", self.state)
        self.assertEqual({}, self.state["human_reviews"])
        self.assertTrue(self.state["validation_archive"])
        self.assertIn("Support Unicode names", lifecycle.render(self.state))
        with self.assertRaises(s.Paused): g.execution_guard(self.state)

    def test_in_place_tampering_and_model_approval_claim_do_not_authorize_execution(self):
        self.approve()
        self.state["goal_contract"]["body"]["scope_exclusions"] = []
        self.assertFalse(g.approved(self.state))
        self.draft()
        self.state["goal_contract"]["approval_status"] = "approved"
        self.assertFalse(g.approved(self.state))

    def test_execution_escalates_each_material_issue_via_astra_and_keeps_artifact(self):
        for kind in ["clarification", "contradiction", "infeasible", "permission", "goal_change"]:
            with self.subTest(kind=kind):
                self.setUp()
                self.approve()
                lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
                value = {**envelope(self.state), "summary": "Useful partial work", "user_request": {
                    "kind": kind, "discovered": "Need an external service", "impact": "Current scope prohibits it",
                    "decision_needed": "Choose local storage or authorize the service", "options": ["Local: no service", "Service: credentials needed"],
                    "proposed_delta": "Add service permission"}}
                runner.apply_result(self.state, "terra", value, {"stage": "terra", "output": "saved", "changed_files": ["greet.py"]}, self.root, self.run)
                self.assertEqual("astra_review", self.state["next_stage"])
                self.assertEqual("RUNNING", self.state["status"])
                self.assertEqual("Useful partial work", self.state["implementation"]["summary"])
                decision = {**self.decision("BLOCKED"), "user_request": value["user_request"]}
                output = self.run / f"blocked-{kind}.json"
                s.atomic_json(output, decision)
                runner.apply_result(self.state, "astra_review", decision,
                                    {"stage": "astra_review", "output": str(output), "source_revision": s.snapshot(self.root)["revision"]},
                                    self.root, self.run)
                self.assertEqual("Useful partial work", self.state["implementation"]["summary"])
                if kind in ("permission", "goal_change"):
                    # A material decision is published to the user at the writer boundary.
                    self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
                    self.assertEqual("WAITING_FOR_USER", self.state["phase"])
                    self.assertEqual(kind, lifecycle.human.current(self.state)["scope"])
                    self.assertEqual(2, self.invoke("--resume-paused"))
                    self.assertEqual("WAITING_FOR_USER", self.state["status"])
                else:
                    # A clarification or blocker first goes to AutoResolver's read-only
                    # diagnosis; the user is not asked and no builder is dispatched.
                    runner.normalize_human_boundary(self.state, self.run)
                    self.assertEqual("RESOLVING", self.state["phase"])
                    self.assertEqual("astra_resolve", self.state["next_stage"])
                    self.assertIsNone(lifecycle.human.current(self.state))
                    launched = []
                    def diagnose(**kwargs):
                        launched.append(kwargs)
                        raise s.Paused("PAUSED_TEST_LAUNCH", "Mock diagnosis admitted; no provider called")
                    self.assertEqual(2, self.invoke("--resume-paused", role=diagnose))
                    self.assertEqual(["astra_resolve"], [call["state"]["next_stage"] for call in launched])
                    self.assertFalse(launched[0]["allow_write"])
                    self.assertEqual("read-only", launched[0]["sandbox"])
                self.assertEqual("Useful partial work", self.state["implementation"]["summary"])

    def test_goal_change_answer_requires_new_revision_and_approval(self):
        self.approve()
        before = g.token(self.state["goal_contract"])
        lifecycle.wait_for_user(self.state, {"kind": "goal_change", "decision_needed": "Allow Unicode?", "impact": "Changes scope",
                                   "discovered": "Names include non-ASCII", "options": ["Yes", "No"], "proposed_delta": "Accept Unicode"})
        lifecycle.human.evaluate(self.state)
        g.answer(self.state, self.state["pending_questions"][0]["id"], "Yes")
        self.assertFalse(g.approved(self.state))
        revised = body(); revised["required_behaviors"].append("Accept Unicode")
        lifecycle.install_draft(self.state, revised, origin="astra_discovery")
        self.assertNotEqual(before, g.token(self.state["goal_contract"]))
        with self.assertRaises(ValueError): lifecycle.approve(self.state, before)

    def test_optional_backlog_does_not_block_completion(self):
        self.approve()
        current = self.validation()
        decision = self.decision("TASK_COMPLETE")
        decision["deferred_backlog"] = ["Optional web UI", "Optional colours"]
        runner.apply_result(self.state, "astra_review", decision, {"output": "final"}, self.root, self.run)
        self.assertEqual("COMPLETE", self.state["phase"])
        self.assertEqual(2, len(self.state["deferred_backlog"]))

    def test_cannot_start_optional_work_once_goal_has_current_passing_evidence(self):
        self.approve(); self.validation()
        # First CONTINUE for this artifact: the Completion Owner is sent back once, told why.
        runner.apply_result(self.state, "astra_review", self.decision(), {"output": "extra"}, self.root, self.run)
        self.assertEqual(("RUNNING", "astra_review"), (self.state["status"], self.state["next_stage"]))
        self.assertIn("Return TASK_COMPLETE", self.state["stop_reason"])
        prompt, _ = stage_context.context_packet(self.state, "astra_review", self.run / "state.json")
        self.assertIn(json.dumps(self.state["stop_reason"]), prompt)
        # CONTINUE again for the same artifact: the user decides.
        runner.apply_result(self.state, "astra_review", self.decision(), {"output": "extra"}, self.root, self.run)
        self.assertEqual("PAUSED_COMPLETION_REVIEW", self.state["status"])
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertNotIn("active_stage", self.state)

    def test_repeated_validation_consumes_iteration_budget(self):
        self.approve()
        initial = self.state["iteration"]
        runner.apply_result(self.state, "astra_review", self.decision("VALIDATE"), {"output": "review"}, self.root, self.run)
        self.assertEqual(initial + 1, self.state["iteration"])
        self.assertEqual("sol", self.state["next_stage"])

    def test_completion_requires_current_artifact_all_evidence_and_actual_human_approval(self):
        self.approve(human=True)
        current = self.validation()
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        self.publish_review()
        lifecycle.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        self.assertTrue(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        for status in ["FAIL", "UNVERIFIED", "SKIPPED", "AWAITING_USER"]:
            saved = copy.deepcopy(self.state)
            self.state["validation"]["criterion_results"][0]["status"] = status
            self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
            self.state = saved
        (self.root / "greet.py").write_text("changed")
        with self.assertRaises(ValueError):
            g.approve_review(self.state, "C1", g.review_token(self.state), s.snapshot(self.root))
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), s.snapshot(self.root)))

    def test_accept_completion_probe_carries_the_current_task_identity(self):
        """F7: --accept-completion was unreachable whenever a task was assigned."""
        self.approve()
        lifecycle.assign_task(self.state, self.decision(), s.snapshot(self.root))
        self.validation()
        self.assertEqual([], g.missing_human_reviews(self.state))
        task_id = (self.state.get("current_task") or {}).get("id", "")
        self.assertTrue(task_id)
        runner.accept_completion(self.state, self.root)
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertEqual("user_cli", self.state.get("completion_actor"))
        self.assertEqual(task_id, self.state["final_decision"].get("task_id"))

    def test_human_only_pending_validation_closes_only_after_current_review(self):
        self.approve(human=True)
        current = self.validation()
        validation = self.state["validation"]
        validation["verdict"] = "BLOCKED"
        validation["criterion_results"][0]["status"] = "NOT_VERIFIED"
        validation["unverified_criteria"] = ["C1 — awaiting explicit human review"]
        # A review is requested and published (the AutoResolver human protocol) before it can be approved.
        lifecycle.wait_for_user(self.state, {"kind": "human_review", "criteria": ["C1"],
                                     "decision_needed": "Review C1 on the current artifact.",
                                     "impact": "C1 needs human acceptance", "options": ["Approve", "Reject"],
                                     "proposed_delta": ""})
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        selected = g.review_token(self.state)
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        g.approve_review(self.state, "C1", selected, current)
        self.assertTrue(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        self.state["human_reviews"].clear()
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        self.state["validation"]["unverified_criteria"] = ["C2 — unrelated failure"]
        self.assertFalse(g.human_only_pending_validation(self.state, self.state["validation"], "C1"))

    def test_medium_blocking_finding_blocks_even_with_tests_passing(self):
        self.approve(); current = self.validation()
        self.state["validation"]["findings"] = [{"severity": "medium", "blocking": True, "finding": "Required behavior missing"}]
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))

    def test_nonblocking_preference_finding_does_not_prevent_completion(self):
        self.approve(); current = self.validation()
        self.state["validation"]["findings"] = [{"severity": "low", "blocking": False,
                                                    "finding": "Consider renaming this class"}]
        self.assertTrue(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))

    def test_missing_criterion_cannot_hide_behind_green_suite(self):
        self.approve(); current = self.validation()
        self.state["validation"]["criterion_results"] = []
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))

    def test_criterion_cannot_cite_a_fabricated_event_beside_a_real_passing_check(self):
        self.approve(); self.validation()
        value = copy.deepcopy(self.state["validation"])
        value["criterion_results"][0]["evidence_refs"] = ["event:invented"]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "missing executed event"):
            runner.apply_result(self.state, "sol", value, {"events": str(self.run / "sol.jsonl")}, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_changed_evidence_blocks_human_review(self):
        self.approve(human=True); current = self.validation()
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        selected = g.review_token(self.state)
        (self.run / "sol.jsonl").write_text("changed")
        with self.assertRaises(ValueError): g.approve_review(self.state, "C1", selected, current)

    def test_user_command_reloads_checkpoint_after_acquiring_lock(self):
        self.draft()
        @contextlib.contextmanager
        def concurrent_update(workspace):
            saved = s.read(self.run / "state.json")
            saved["user_events"].append({"kind": "concurrent-user-event"})
            s.atomic_json(self.run / "state.json", saved)
            yield
        with patch.object(s, "run_lock", concurrent_update):
            self.assertEqual(0, self.invoke("--show-goal"))
        self.assertIn({"kind": "concurrent-user-event"}, self.state["user_events"])

    def test_safe_boundary_feedback_is_applied_once_and_requires_explicit_continue(self):
        self.approve()
        s.atomic_json(self.run / "state.json", self.state)
        receipt = interventions.submit(self.root, self.run, request_id="change-1", kind="feedback",
                                       text="Retain the partial implementation")
        self.assertTrue(runner.consume_interventions(self.state, self.run, self.root))
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertFalse(g.approved(self.state))
        self.assertEqual("intervention-change-1", self.state["brief_feedback"][-1]["id"])
        self.assertEqual(["change-1"], [item["id"] for item in self.state["applied_interventions"]])
        self.assertEqual([], interventions.inspect(self.root, self.run)["requests"])
        self.assertFalse(runner.consume_interventions(self.state, self.run, self.root))
        self.assertEqual(receipt["receipt"]["text"], self.state["applied_interventions"][0]["text"])

    def test_pause_only_preserves_approval_and_has_an_acknowledgement_lifecycle(self):
        self.approve()
        prior_stage = self.state["next_stage"]
        s.atomic_json(self.run / "state.json", self.state)
        interventions.submit(self.root, self.run, request_id="pause-1", kind="pause", text="")
        self.assertTrue(runner.consume_interventions(self.state, self.run, self.root))
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
        self.assertEqual(prior_stage, self.state["next_stage"])
        self.assertTrue(g.approved(self.state))
        self.assertIsNone(self.state["pause_intent"]["acknowledged_at"])
        self.assertEqual(2, self.invoke())
        (self.run / "pause-requested").write_text("fixture")
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.assertIsNotNone(self.state["pause_intent"]["acknowledged_at"])

    def test_stale_role_result_and_criterion_weakening_are_transactionally_rejected(self):
        self.approve()
        saved = copy.deepcopy(self.state)
        result = self.decision(); result["contract_revision"] -= 1
        with self.assertRaises(s.Paused): runner.apply_result(self.state, "astra_plan", result, {}, self.root, self.run)
        self.assertEqual(saved, self.state)
        result = self.decision(); result["acceptance_criteria"][0]["criterion"] = "Easier goal"
        with self.assertRaises(s.Paused): runner.apply_result(self.state, "astra_plan", result, {}, self.root, self.run)
        self.assertEqual(saved, self.state)

    def test_user_decisions_cannot_be_invented(self):
        proposed = body()
        proposed["delegated_decisions"] = [{"text": "Use web", "basis": "delegated", "answer_id": "invented"}]
        with self.assertRaises(ValueError): lifecycle.install_draft(self.state, proposed, origin="agent")
        self.draft(questions=True)
        lifecycle.human.evaluate(self.state)
        g.answer(self.state, "Q1", "accept default", delegated=True)
        proposed["delegated_decisions"][0]["answer_id"] = "Q1"
        lifecycle.install_draft(self.state, proposed, origin="agent")
        self.assertFalse(g.approved(self.state))

    def test_inferred_assumption_must_not_cite_an_answer_id(self):
        proposed = body()
        proposed["accepted_assumptions"] = [{"text": "Plan is the baseline", "basis": "original_request", "answer_id": "original-request"}]
        with self.assertRaises(ValueError): lifecycle.install_draft(self.state, proposed, origin="agent")
        proposed["accepted_assumptions"][0]["answer_id"] = ""
        lifecycle.install_draft(self.state, proposed, origin="agent")
        self.assertFalse(g.approved(self.state))

    def test_migration_retains_work_sessions_limits_and_does_not_approve(self):
        legacy = copy.deepcopy(self.state)
        legacy.update(version=2, next_stage="sol", sessions={"terra": "existing-session"},
                      implementation={"summary": "completed batch"}, changed_files=["greet.py"], iteration=4)
        legacy["validation"] = {"verdict": "PASS"}
        legacy["stages"] = [{"output": "saved-terra.json"}]
        legacy["answers"] = {"saved-question": {"text": "Known answer", "kind": "answer"}}
        lifecycle.migrate(legacy)
        self.assertEqual("existing-session", legacy["sessions"]["terra"])
        self.assertEqual("completed batch", legacy["implementation"]["summary"])
        self.assertEqual(4, legacy["iteration"])
        self.assertEqual("sol", legacy["pre_goal_checkpoint"]["next_stage"])
        self.assertEqual("astra_discovery", legacy["next_stage"])
        self.assertFalse(g.approved(legacy))
        self.assertEqual("saved-terra.json", legacy["stages"][0]["output"])
        self.assertTrue(legacy["validation_archive"])
        self.assertEqual("Known answer", legacy["answers"]["saved-question"]["text"])
        self.assertIn("Reconstruct the goal from the request and saved work", lifecycle.render(legacy))

    def test_active_or_uncertain_migration_refuses_without_discarding_work(self):
        for field in ["active_stage", "uncertain_artifacts"]:
            legacy = {**self.state, "version": 2, field: "still pending"}
            before = copy.deepcopy(legacy)
            with self.assertRaises(s.Paused): lifecycle.migrate(legacy)
            self.assertEqual(before, legacy)

    def test_limits_pause_and_cannot_complete(self):
        self.approve()
        for name, value, expected in [("iteration_ceiling", 0, "PAUSED_ITERATION_LIMIT"),
                                      ("max_seconds", 1, "PAUSED_TIME_LIMIT"),
                                      ("no_progress_batches", 1, "PAUSED_NO_PROGRESS")]:
            with self.subTest(name=name):
                saved = copy.deepcopy(self.state)
                self.state["settings"]["limits"][name] = value
                self.state.update(active_seconds=1, no_progress_batches=1,
                                  stages=[{"metrics": {"provider_tokens": {"input_tokens": 1, "output_tokens": 1}}}])
                self.assertEqual(2, self.invoke())
                # The limit pause is now an AutoResolver operational request that keeps
                # the original pause status as its origin; the run still cannot continue.
                assert_operational_wait(self, self.state, expected)
                self.assertNotEqual("TASK_COMPLETE", self.state["status"])
                self.state = saved

    def test_interrupted_abandoned_unknown_usage_retains_work_and_allows_resume(self):
        self.approve()
        self.state["settings"]["limits"]["max_reported_tokens"] = 100
        base = self.run / "iterations/001/terra-01"
        base.parent.mkdir(parents=True)
        s.atomic_json(base.with_suffix(".before.json"), s.snapshot(self.root))
        raw_events = '{"type":"thread.started","thread_id":"interrupted-session"}\n'
        base.with_suffix(".jsonl").write_text(raw_events)
        (self.root / "partial.py").write_text("# retained partial work\n")
        self.state.update(status="PAUSED_INTERRUPTED", phase="PAUSED_OR_BLOCKED", next_stage="terra",
            active_stage={"role": "terra", "stage": "terra", "iteration": 1, "duration_seconds": 8,
                          "output": str(base.with_suffix(".json")), "events": str(base.with_suffix(".jsonl")),
                          "before_ref": str(base.with_suffix(".before.json")), "exit_code": -15})
        self.state["sessions"]["terra"] = "interrupted-session"
        self.assertEqual(2, self.invoke("--resume-paused"))
        assert_operational_wait(self, self.state, "PAUSED_PROVIDER_UNCERTAIN")
        self.assertIn("--abandon-stage 001/terra-01", self.stderr)
        self.assertEqual(0, self.invoke("--abandon-stage", "001/terra-01"))
        self.assertEqual("PAUSED_STAGE_ABANDONED", self.state["status"])
        self.assertNotIn("active_stage", self.state)
        self.assertNotIn("terra", self.state["sessions"])
        archived = copy.deepcopy(self.state["stages"])
        # The saved stages also hold AutoResolver's runner-owned diagnostic receipt.
        abandoned = [row for row in archived if not row.get("runner_owned")]
        self.assertEqual(1, len(abandoned))
        self.assertTrue(abandoned[0]["abandoned"])
        self.assertIsNone(abandoned[0]["metrics"]["provider_tokens"]["input_tokens"])
        self.assertIsNone(abandoned[0]["metrics"]["provider_tokens"]["output_tokens"])
        events = abandoned[0]["events"]
        self.assertNotEqual(str(base.with_suffix(".jsonl")), events)
        calls = []
        def admitted(**kwargs):
            calls.append(kwargs)
            raise s.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
        self.assertEqual(2, self.invoke("--resume-paused", role=admitted))
        self.assertEqual(1, len(calls))
        self.assertEqual("PAUSED_TEST_LAUNCH", self.state["status"])
        self.assertNotIn("max_reported_tokens", self.state["settings"]["limits"])
        self.assertEqual(archived, self.state["stages"][:len(archived)])
        self.assertEqual(raw_events, Path(events).read_text())
        self.assertEqual("# retained partial work\n", (self.root / "partial.py").read_text())
        self.assertEqual(8, self.state["active_seconds"])

    def test_saved_token_pause_can_resume_without_a_new_spending_allowance(self):
        self.approve()
        baseline = copy.deepcopy(self.state)
        human = runner.resolver_runtime.human
        for status in ("PAUSED_BUDGET", "PAUSED_USAGE_UNKNOWN"):
            with self.subTest(status=status):
                self.state = copy.deepcopy(baseline)
                self.state["settings"]["limits"]["max_reported_tokens"] = 1
                self.state["settings"]["budget_origins"] = {"max_reported_tokens": "user_explicit"}
                self.state.update(status=status, stop_reason="Saved token guard exhausted")
                # Construct a saved legacy request with a genuine runner receipt.
                runner.resolver_runtime.record_operational_exhaustion(
                    runner, self.state, self.run, s.Paused("PAUSED_BUDGET", self.state["stop_reason"]))
                proposal = copy.deepcopy(self.state[human.PRIVATE])
                human.queue(self.state, "operational_exhaustion",
                    {"stage": "terra", "pause_status": status, "budget": {"kind": "max_reported_tokens"}},
                    request=proposal["request"], evidence=proposal["evidence"])
                self.assertEqual("escalate", human.evaluate(self.state))
                calls = []
                def admitted(**kwargs):
                    calls.append(kwargs)
                    raise s.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
                self.assertEqual(2, self.invoke("--resume-paused", role=admitted))
                self.assertEqual(1, len(calls))
                self.assertEqual("PAUSED_TEST_LAUNCH", self.state["status"])
                self.assertNotIn(human.PUBLIC, self.state)
                self.assertEqual(baseline["goal_contract"], self.state["goal_contract"])

    def test_saved_token_setting_does_not_bypass_plan_approval(self):
        self.draft()
        self.state["settings"]["limits"]["max_reported_tokens"] = 1
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertFalse(g.approved(self.state))

    def test_legacy_token_cap_does_not_stop_known_or_unknown_usage(self):
        self.approve()
        baseline = copy.deepcopy(self.state)
        for tokens in ({"input_tokens": 2000000, "output_tokens": 100},
                       {"input_tokens": None, "output_tokens": None}):
            with self.subTest(tokens=tokens):
                self.state = copy.deepcopy(baseline)
                self.state["settings"]["limits"]["max_reported_tokens"] = 1
                attempt = {"metrics": {"provider_tokens": tokens}, "output": "saved-attempt.json"}
                self.state["stages"].append(attempt)
                calls = []
                def admitted(**kwargs):
                    calls.append(kwargs)
                    raise s.Paused("PAUSED_TEST_LAUNCH", "Offline stage admission verified")
                self.assertEqual(2, self.invoke(role=admitted))
                self.assertEqual(1, len(calls))
                self.assertEqual("PAUSED_TEST_LAUNCH", self.state["status"])
                self.assertNotIn("max_reported_tokens", self.state["settings"]["limits"])
                self.assertEqual(attempt, self.state["stages"][0])


    def test_cli_approve_saves_ready_without_launching(self):
        self.draft()
        self.invoke("--show-goal")
        self.assertEqual(0, self.invoke("--approve-goal", g.token(self.state["goal_contract"])))
        self.assertEqual("READY_TO_EXECUTE", self.state["phase"])

    def test_show_goal_explains_the_approval_it_asks_for(self):
        # Issue #381: the plan approval says what the revision and its token are, what
        # approving authorizes and the limits in effect, and names the command that approves.
        self.draft()
        self.state["settings"]["limits"].update(max_seconds=43200, stage_timeout_seconds=3600)
        self.assertEqual(0, self.invoke("--show-goal"))
        contract, shown = self.state["goal_contract"], self.stdout
        selected = g.token(contract)
        revision = contract["revision"]
        self.assertIn(f"Plan revision {revision} (r{revision}) waits for your approval. Approving it "
                      "authorizes implementation", shown)
        self.assertIn("SHA-256 lock on this exact plan", shown)
        self.assertIn("Limits in effect: 12 h of active time for the run, 1 h per stage, "
                      "stops after iteration 5 (now at 1), one Builder at a time.", shown)
        # The token line keeps its exact format: tools and live checks parse it.
        self.assertEqual([selected], re.findall(r"^Approval token: ([^\n]*)$", shown, re.MULTILINE))
        commands = re.findall(r"^To approve this plan: (.*)$", shown, re.MULTILINE)
        self.assertEqual([f"autocode --run-dir {shlex.quote(str(self.run))} --approve-goal {selected}"], commands)
        self.assertEqual(0, self.invoke(*shlex.split(commands[0])[1:]))
        self.assertTrue(g.approved(self.state))

    def test_show_goal_ends_with_what_the_approval_decides(self):
        # Issue #381: the full brief runs to hundreds of lines, so the last screen of the approval
        # stop summarizes the decision, right above the limits and the approve command.
        self.draft(human=True)
        self.assertEqual(2, self.invoke("--no-chat"))  # the stop a plain run prints (present)
        stop = self.stdout
        self.assertEqual(0, self.invoke("--show-goal"))
        revision = self.state["goal_contract"]["revision"]
        decision = "\n".join([
            f"Before you approve r{revision} (a summary of the plan above):",
            "What it will do:", "  Provide a deterministic greeting CLI",
            "Built in 1 milestone: M1.",
            "What it may change:", "  - Read and edit only this fixture Git workspace; no external writes",
            "Out of scope:", "  - Web service", "  - Deployment",
            "Done when:", "  [C1] Contract holds",
            "    Checked by: Execute greeting and invalid-input regression checks",
            "    Also needs your review of the result before the run can complete.",
            "What passing proves:",
            "  - An independent check of the final source must pass every criterion above (it may leave those "
            "marked for your review to you), and the runner itself re-runs that check's commands in a clean copy: "
            "each must exit 0.",
            "  - No criterion is marked test: or guard:, so nothing shows that a check would fail without the change.",
            "  - Not proven: behavior no criterion describes, or inputs no check exercises.",
            "", "Limits in effect: "])
        for shown in (stop, self.stdout):
            self.assertEqual(1, shown.count(decision))
            # Below the full brief, whose sections keep their order and text.
            self.assertLess(shown.index("\nAcceptance criteria:\n  [C1] Contract holds\n    Verify: "),
                            shown.index(decision))
            self.assertLess(shown.index("\nMilestones:\n  [M1] "), shown.index(decision))
            # Then only the limits, the two commands and the state line remain of the brief.
            tail = shown[shown.index(decision) + len(decision):].splitlines()
            self.assertTrue(tail[1].startswith("To approve this plan: autocode --run-dir "))
            self.assertTrue(tail[2].startswith("To change it instead: "))
            self.assertEqual(["", "State: AWAITING_GOAL_APPROVAL / AWAITING_GOAL_APPROVAL"], tail[3:5])
            self.assertEqual([g.token(self.state["goal_contract"])],
                             re.findall(r"^Approval token: ([^\n]*)$", shown, re.MULTILINE))
        # Nothing to decide, nothing summarized: once approved, and at a question stop.
        self.assertEqual(0, self.invoke("--approve-goal", g.token(self.state["goal_contract"])))
        self.assertEqual(0, self.invoke("--show-goal"))
        self.assertNotIn("Before you approve", self.stdout)
        self.assertNotIn("What passing proves:", self.stdout)
        self.draft(questions=True)
        self.invoke("--no-chat")
        self.assertEqual(0, self.invoke("--show-goal"))
        self.assertIn("Should the greeting be a CLI or web endpoint?", self.stdout)
        self.assertNotIn("Before you approve", self.stdout)
        self.assertNotIn("Approval token:", self.stdout)

    def test_approval_stop_states_what_the_regression_proof_will_require(self):
        # A bug fix planned without an investigation: the proof falls back to the plan's test:/guard:
        # criteria, and the approval stop names them with what each test must show.
        plan = body(task_kind="bugfix")
        plan["acceptance_criteria"] += [
            {"id": "C2", "criterion": "Greets Ada", "verification_method": "test: test_c2_greets_ada",
             "human_review": False},
            {"id": "C3", "criterion": "Still rejects an empty name", "verification_method": "guard: test_c3_empty",
             "human_review": False}]
        plan["milestones"][0]["acceptance_criteria"] += ["C2", "C3"]
        lifecycle.install_draft(self.state, plan, origin="test")
        self.assertEqual(2, self.invoke("--no-chat"))
        self.assertIn("\n  - The runner also runs the test each of these criteria names: C2 (test:) must fail on the "
                      "original code and pass with the fix; C3 (guard:) must pass with the change and on the original "
                      "code.\n  - If the test for C3 cannot load on the original code", self.stdout)
        # A design job: no test a criterion names is run as proof, and no part of the brief says otherwise.
        plan = body()
        plan["acceptance_criteria"][0]["verification_method"] = "test: test_c1_greets"
        self.state["workflow"] = {"kind": "design", "reason": "a design", "signals": [], "source": "model"}
        lifecycle.install_draft(self.state, plan, origin="test")
        self.assertEqual(2, self.invoke("--no-chat"))
        self.assertIn("\nJob type: design (not a bug fix): no test named in a criterion is run as proof, so nothing "
                      "shows that a check would fail without the change.\n", self.stdout)
        self.assertIn("\n  - Design job: no test named in a criterion is run as proof", self.stdout)
        self.assertNotIn("proven by the runner", self.stdout)
        self.assertNotIn("The runner also runs", self.stdout)

    def test_brief_shows_a_structured_field_as_lines_not_json(self):
        task = {"objective": "Build the greeting", "affected_paths": ["greet.py", "test_greeting.py"],
                "kind": "implement", "milestone_id": "M1", "requirements": [], "acceptance_criteria": ["C1"],
                "validation_plan": ["python3 -m unittest"]}
        state = {"status": "AWAITING_GOAL_APPROVAL", "phase": "AWAITING_GOAL_APPROVAL",
                 "goal_contract": {"revision": 2, "approval_status": "draft", "body": {"initial_task": task}}}
        shown = lifecycle.render(state)
        self.assertIn("Initial task:\n  Objective: Build the greeting\n  Affected paths:\n    - greet.py\n"
                      "    - test_greeting.py\n  Kind: implement\n  Milestone id: M1\n  Requirements: (none)\n"
                      "  Acceptance criteria:\n    - C1\n  Validation plan:\n    - python3 -m unittest\n", shown)
        self.assertNotIn("{", shown)
        self.assertIn("Verification obligations (declarations, not execution proof):", shown)
        self.assertIn("Collected test ids:\n        (unmeasured)", shown)
        self.assertIn("Execution inventory complete:\n    no", shown)
        self.assertIn("global runtimes execute fresh", shown)

    def test_cli_can_approve_reviewed_goal_after_unapproved_resume_pause(self):
        self.draft()
        self.invoke("--show-goal")
        selected = g.token(self.state["goal_contract"])
        self.state.update(status="PAUSED_GOAL_UNAPPROVED", phase="PAUSED_OR_BLOCKED")
        self.assertEqual(0, self.invoke("--approve-goal", selected))
        self.assertTrue(g.approved(self.state))
        self.assertEqual("READY_TO_EXECUTE", self.state["phase"])

    def test_paused_goal_approval_rejects_unreconciled_work_and_stale_tokens(self):
        for field in ("active_stage", "uncertain_artifacts", "pending_report_repair"):
            with self.subTest(field=field):
                self.draft()
                lifecycle.human.evaluate(self.state)
                lifecycle.present(self.state)
                selected = g.token(self.state["goal_contract"])
                self.state.update(status="PAUSED_GOAL_UNAPPROVED", **{field: {"pending": True}})
                with self.assertRaises(ValueError):
                    lifecycle.approve(self.state, selected)
                self.state.pop(field)
        self.draft()
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        self.state.update(status="PAUSED_GOAL_UNAPPROVED")
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, "stale-token")

    def test_runner_guard_rejects_unapproved_stage_before_subprocess(self):
        self.draft()
        lifecycle.human.evaluate(self.state)
        self.state.update(next_stage="terra", status="RUNNING", phase="EXECUTING")
        self.assertEqual(2, self.invoke("--resume-paused"))
        # The guard's PAUSED_GOAL_UNAPPROVED pause is reconciled at the writer boundary
        # into a published request to approve the exact draft; nothing was launched.
        self.assertEqual("Current goal revision has no valid explicit approval", self.state["stop_reason"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertEqual("goal_approval", lifecycle.human.current(self.state)["scope"])
        self.assertFalse(g.approved(self.state))
        self.assertFalse([row for row in self.state["stages"] if row.get("stage") == "terra"])

    def test_user_cannot_combine_answer_and_approval(self):
        self.draft(questions=True)
        with self.assertRaises(SystemExit): self.invoke("--answer", "Q1=CLI", "--approve-goal", "anything")

    def test_new_brief_requires_flow_approach_and_milestones_covering_criteria(self):
        for field in g.BRIEF_FIELDS:
            draft = body(); draft[field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                lifecycle.install_draft(self.state, draft, origin="test")
        draft = body(); draft["milestones"][0]["acceptance_criteria"] = ["invented"]
        with self.assertRaises(ValueError): lifecycle.install_draft(self.state, draft, origin="test")
        draft = body(); draft["acceptance_criteria"].append({**draft["acceptance_criteria"][0], "id": "C2"})
        with self.assertRaises(ValueError): lifecycle.install_draft(self.state, draft, origin="test")

    def test_existing_v3_brief_can_be_approved_without_rewriting_its_contract(self):
        self.draft()
        contract = self.state["goal_contract"]
        for field in g.BRIEF_FIELDS:
            del contract["body"][field]
        contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        original = copy.deepcopy(contract["body"])
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, g.token(contract))
        self.assertTrue(g.approved(self.state))
        self.assertEqual(original, contract["body"])

    def test_completed_legacy_discovery_recovers_using_its_saved_schema(self):
        draft = body()
        for field in g.BRIEF_FIELDS:
            del draft[field]
        schema = self.run / "old-discovery-schema.json"
        s.atomic_json(schema, g.obj({"contract": g.LEGACY_BODY_SCHEMA, "summary": g.STRING}))
        result = {"contract": draft, "summary": "Previously completed interview"}
        runner.apply_result(self.state, "astra_discovery", result,
                            {"schema": str(schema), "output": "saved-discovery"}, self.root, self.run)
        self.assertEqual(draft, self.state["goal_contract"]["body"])
        # The draft is queued; the runner's writer boundary publishes the approval request.
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        lifecycle.human.evaluate(self.state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertFalse(g.approved(self.state))

    def test_brief_feedback_is_saved_as_input_and_requires_a_new_approval(self):
        self.draft()
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        old = g.token(self.state["goal_contract"])
        self.assertEqual(0, self.invoke("--feedback", "Keep Unicode names in the first milestone"))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertFalse(g.approved(self.state))
        with self.assertRaises(ValueError): lifecycle.approve(self.state, old)
        prompt, _ = stage_context.context_packet(self.state, "astra_discovery", self.run / "state.json")
        self.assertIn("Keep Unicode names", prompt)
        event = self.state["brief_feedback"][0]
        draft = body()
        draft["accepted_assumptions"].append({"text": event["text"], "basis": "user_feedback", "answer_id": event["id"]})
        lifecycle.install_draft(self.state, draft, origin="astra_discovery")
        lifecycle.human.evaluate(self.state)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertNotEqual(old, g.token(self.state["goal_contract"]))

    def test_empty_feedback_and_combined_feedback_approval_are_rejected(self):
        self.draft()
        before = copy.deepcopy(self.state)
        self.assertEqual(2, self.invoke("--feedback", "  "))
        self.assertEqual(before, self.state)
        with self.assertRaises(SystemExit): self.invoke("--feedback", "Change scope", "--approve-goal", "anything")

    def test_chat_feedback_keeps_case_and_does_not_authorize_build(self):
        self.draft()
        with patch("builtins.input", return_value="Keep Unicode Support"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(runner.chat_checkpoint(self.state))
        self.assertEqual("Keep Unicode Support", self.state["brief_feedback"][0]["text"])
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertFalse(g.approved(self.state))

    def test_chat_interruption_preserves_prior_answers_and_never_approves(self):
        draft = body(questions=True)
        draft["open_blocking_questions"].append({**draft["open_blocking_questions"][0], "id": "Q2"})
        lifecycle.install_draft(self.state, draft, origin="test")
        with patch("builtins.input", side_effect=["CLI", KeyboardInterrupt]), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(runner.chat_checkpoint(self.state))
        self.assertEqual("CLI", self.state["answers"]["Q1"]["text"])
        self.assertEqual(["Q2"], [q["id"] for q in self.state["pending_questions"]])
        self.assertFalse(g.approved(self.state))

    def test_chat_answers_do_not_bypass_pause_after_stage(self):
        calls = []
        def interview(**kwargs):
            calls.append(kwargs["state"]["next_stage"])
            return {"contract": body(questions=True), "summary": "Confirm the interface"}, {
                "output": str(self.run / "draft.json"), "duration_seconds": 0.01}
        with patch("builtins.input", return_value="CLI"):
            self.assertEqual(2, self.invoke("--chat", "--pause-after-stage", role=interview))
        self.assertEqual(["astra_discovery"], calls)
        self.assertEqual("PAUSED_REQUESTED", self.state["status"])
        self.assertEqual("CLI", self.state["answers"]["Q1"]["text"])
        self.assertFalse(g.approved(self.state))

    def test_chat_human_review_returns_to_astra_instead_of_spinning(self):
        self.approve(human=True); self.validation()
        runner.apply_result(self.state, "astra_review", self.decision("COMPLETE"), {"output": "review"}, self.root, self.run)
        with patch("builtins.input", return_value="yes"), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(runner.chat_checkpoint(self.state))
        self.assertEqual("astra_review", self.state["next_stage"])
        self.assertEqual([], g.missing_human_reviews(self.state))
        self.assertNotEqual("COMPLETE", self.state["phase"])

    def test_artifact_approval_closes_its_question_and_is_idempotent(self):
        self.approve(human=True)
        current = self.validation()
        request = {"kind": "human_review", "criteria": ["C1"],
                   "decision_needed": "Review C1 on the current artifact.",
                   "impact": "C1 needs human acceptance", "options": ["Approve", "Reject"],
                   "proposed_delta": ""}
        lifecycle.wait_for_user(self.state, request)
        lifecycle.human.evaluate(self.state)
        question = copy.deepcopy(self.state["pending_questions"][0])
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        selected = g.review_token(self.state)
        self.assertEqual(["C1"], question["review_criteria"])
        self.assertEqual(selected, question["review_token"])
        g.approve_review(self.state, "C1", selected, current)
        self.assertEqual([], self.state["pending_questions"])
        self.assertNotIn("user_request", self.state)
        self.assertEqual("RUNNING", self.state["status"])
        saved = copy.deepcopy(self.state)
        g.approve_review(self.state, "C1", selected, current)
        self.assertEqual(saved, self.state)
        with self.assertRaises(ValueError):
            g.answer(self.state, question["id"], "Approve again")
        self.assertEqual(saved, self.state)

    def legacy_review_fixture(self, saved_by_runner=False):
        self.approve(human=True)
        if saved_by_runner:
            # The CLI answers a request the runner published: let the runner save this
            # run once (run directory and settings) before AutoResolver publishes it.
            self.state.update(status="PAUSED_FIXTURE", phase="PAUSED_OR_BLOCKED")
            self.assertEqual(2, self.invoke())
            self.state.update(status="RUNNING", phase="READY_TO_EXECUTE")
        current = self.validation()
        original_id = "original-review"
        question = {"id": original_id, "question": "Record the required C1 decision: accept or reject the result.",
                    "options": ["Accept C1: record acceptance.", "Reject C1: request correction."]}
        original = {"kind": "permission_answer", "actor": "user_cli", "at": "2026-09-23T10:00:00Z",
                    "question_id": original_id, "question": question,
                    "contract_token": g.token(self.state["goal_contract"]),
                    "text": "Accept C1. I reviewed the result."}
        carry = {"kind": "permission_answer", "actor": "user_cli", "at": "2026-09-24T10:00:00Z",
                 "question_id": "carry-review", "contract_token": g.token(self.state["goal_contract"]),
                 "text": "Preserve the existing C1 acceptance; do not request another human visual approval."}
        self.state.setdefault("user_events", []).extend([original, carry])
        self.state.setdefault("answers", {}).update({original_id: original, "carry-review": carry})
        request = {"kind": "blocker", "decision_needed": "Reconcile existing C1 acceptance with the runner gate.",
                   "proposed_delta": "No contract, criterion, source or permission change."}
        # The saved reconciliation blocker reaches the user only after AutoResolver's
        # read-only diagnosis could not resolve it internally; it then publishes it.
        diagnosis = self.run / "resolver-diagnosis.json"
        s.atomic_json(diagnosis, {"diagnosis": "Only the user's saved acceptance can be reconciled"})
        self.state["stages"].append({"stage": "astra_resolve", "output": str(diagnosis),
                                     "exit_code": 0, "changed_files": []})
        lifecycle.human.queue(self.state, "blocker", {"stage": "astra_resolve", "output": str(diagnosis)},
                      request=request,
                      questions=[{"id": "runner-reconcile", "question": request["decision_needed"]}],
                      evidence={"diagnosis": "Only the user's saved acceptance can be reconciled",
                                "output": str(diagnosis)},
                      next_stage="astra_review")
        self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        lifecycle.present(self.state)
        return current, original_id

    def test_legacy_review_reconciliation_preserves_the_original_user_event(self):
        current, original_id = self.legacy_review_fixture()
        old_user_events = [event for event in self.state["user_events"] if event.get("actor") == "user_cli"]
        g.reconcile_legacy_review(self.state, "C1", original_id, g.review_token(self.state), current)
        self.assertEqual([], g.missing_human_reviews(self.state))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual([], self.state["pending_questions"])
        self.assertNotIn("user_request", self.state)
        self.assertEqual(old_user_events, [event for event in self.state["user_events"] if event.get("actor") == "user_cli"])
        self.assertEqual("review_reconciliation", self.state["human_reviews"]["C1"]["kind"])
        self.assertEqual("runner", self.state["human_reviews"]["C1"]["actor"])
        self.assertFalse(g.missing_human_reviews(self.state))
        self.state["validation"]["source_revision"] = "changed"
        self.assertEqual(["C1"], g.missing_human_reviews(self.state))

    def test_legacy_review_reconciliation_rejects_forged_or_missing_provenance(self):
        current, original_id = self.legacy_review_fixture()
        for change in (lambda state: state["user_events"].remove(state["answers"][original_id]),
                       lambda state: state["answers"][original_id].update(text="Reject C1."),
                       lambda state: state["answers"].pop("carry-review"),
                       lambda state: state["validation"].update(verdict="FAIL")):
            candidate = copy.deepcopy(self.state)
            change(candidate)
            with self.assertRaises(ValueError):
                g.reconcile_legacy_review(candidate, "C1", original_id, g.review_token(candidate), current)
            self.assertNotIn("C1", candidate.get("human_reviews", {}))

    def test_cli_legacy_review_reconciliation_saves_without_launching_a_provider(self):
        current, original_id = self.legacy_review_fixture(saved_by_runner=True)
        token = g.review_token(self.state)
        with patch.object(s, "snapshot", return_value=current):
            self.assertEqual(0, self.invoke("--reconcile-review", f"C1={original_id}", "--review-token", token))
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual([], g.missing_human_reviews(self.state))

    def test_sql_shaped_legacy_permission_question_closes_on_artifact_approval(self):
        self.approve(human=True)
        current = self.validation()
        request = {"kind": "permission", "decision_needed": "Approve or reject C1 based on the current M5V evidence.",
                   "impact": "M5V cannot advance without human review.",
                   "options": ["Approve C1", "Reject C1"], "proposed_delta": ""}
        lifecycle.wait_for_user(self.state, request)
        # This is how the SQL question was saved before review bindings existed.
        question = self.state[lifecycle.human.PRIVATE]["questions"][0]
        question.pop("review_criteria")
        question.pop("review_token")
        self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
        question = self.state["pending_questions"][0]
        self.assertNotIn("review_token", question)
        lifecycle.present(self.state)
        selected = g.review_token(self.state)
        g.approve_review(self.state, "C1", selected, current)
        self.assertEqual([], self.state["pending_questions"])
        self.assertEqual("RUNNING", self.state["status"])
        with self.assertRaises(ValueError):
            g.answer(self.state, question["id"], "Approve C1")
        self.assertTrue(g.approved(self.state))

    def test_review_approval_keeps_unrelated_question_and_rejects_stale_token(self):
        self.approve(human=True)
        current = self.validation()
        request = {"kind": "human_review", "criteria": ["C1"],
                   "decision_needed": "Review C1 on the current artifact.",
                   "impact": "C1 needs human acceptance", "options": ["Approve", "Reject"],
                   "proposed_delta": ""}
        lifecycle.wait_for_user(self.state, request)
        unrelated = {"id": "other", "question": "Choose a project name", "why": "Needed later",
                     "options": [], "proposed_default": ""}
        # A run publishes one request at a time; the unrelated question travels in the
        # same queued request, so publication shows both questions together.
        self.state[lifecycle.human.PRIVATE]["questions"].append(unrelated)
        self.assertEqual("escalate", lifecycle.human.evaluate(self.state))
        self.assertEqual(2, len(self.state["pending_questions"]))
        lifecycle.present(self.state)
        selected = g.review_token(self.state)
        with self.assertRaises(ValueError):
            g.approve_review(self.state, "C1", "stale", current)
        self.assertEqual(2, len(self.state["pending_questions"]))
        g.approve_review(self.state, "C1", selected, current)
        self.assertEqual([unrelated], self.state["pending_questions"])
        self.assertEqual("WAITING_FOR_USER", self.state["status"])

    def test_one_of_two_review_approvals_closes_question_but_keeps_review_request(self):
        draft = body(human=True)
        draft["acceptance_criteria"].append({"id": "C2", "criterion": "Review a second flow",
            "verification_method": "Inspect the saved flow", "human_review": True})
        draft["milestones"][0]["acceptance_criteria"].append("C2")
        lifecycle.install_draft(self.state, draft, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state["displayed_goal"])
        current = self.validation()
        lifecycle.wait_for_user(self.state, {"kind": "human_review", "criteria": ["C1", "C2"],
            "decision_needed": "Review both criteria.", "impact": "Both need human acceptance",
            "options": ["Approve", "Reject"], "proposed_delta": ""})
        lifecycle.human.evaluate(self.state)
        question_id = self.state["pending_questions"][0]["id"]
        lifecycle.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        self.assertEqual([], self.state["pending_questions"])
        self.assertEqual("WAITING_FOR_USER", self.state["status"])
        self.assertEqual(["C1", "C2"], self.state["user_request"]["criteria"])
        with self.assertRaises(ValueError):
            g.answer(self.state, question_id, "Approve both")

    def test_review_cli_retry_after_restart_keeps_approval_and_goal(self):
        # Keep CLI registry writes out of the source snapshot under review.
        with tempfile.TemporaryDirectory() as registry_home, patch.dict(os.environ, {"AUTOCODE_HOME": registry_home}):
            self.approve(human=True)
            self.validation()
            lifecycle.wait_for_user(self.state, {"kind": "human_review", "criteria": ["C1"],
                "decision_needed": "Review C1 on the current artifact.", "impact": "Approval required",
                "options": ["Approve", "Reject"], "proposed_delta": ""})
            lifecycle.human.evaluate(self.state)
            lifecycle.present(self.state)
            selected = g.review_token(self.state)
            question_id = self.state["pending_questions"][0]["id"]
            self.assertEqual(0, self.invoke("--approve-review", "C1", "--review-token", selected))
            first = copy.deepcopy(self.state)
            self.assertEqual([], first["pending_questions"])
            self.assertEqual(0, self.invoke("--approve-review", "C1", "--review-token", selected))
            self.assertEqual(first["user_events"], self.state["user_events"])
            self.assertEqual(2, self.invoke("--answer", question_id + "=Approve"))
            self.assertEqual(first["goal_contract"], self.state["goal_contract"])
            self.assertEqual("RUNNING", self.state["status"])

    def test_human_review_not_requested_before_passing_automated_evidence(self):
        self.approve(human=True)
        before = copy.deepcopy(self.state)
        with self.assertRaises(s.Paused):
            runner.apply_result(self.state, "astra_review", self.decision("COMPLETE"), {}, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_bounded_task_and_reports_reach_each_role_with_identical_brief(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        assigned = self.state["current_task"]
        self.state["implementation"] = {"commands_run": ["python greet.py Ada"], "results": ["Hello, Ada"],
            "addressed_requirements": assigned["requirements"], "recommended_checks": ["Invalid input"]}
        self.validation()
        for stage in ("terra", "sol", "astra_review"):
            prompt, _ = stage_context.context_packet(self.state, stage, self.run / "state.json")
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertEqual(self.state["goal_contract"], data["goal_contract"])
            self.assertEqual(assigned, data["current_task"])
            self.assertEqual(s.snapshot(self.root)["revision"], data["source_revision"])
            if stage != "terra": self.assertEqual(self.state["implementation"], data["implementation"])
            if stage == "astra_review": self.assertEqual(self.state["validation"], data["validation"])

    def test_unknown_task_milestone_or_criterion_is_rejected_transactionally(self):
        self.approve()
        for field, value in (("milestone_id", "M999"), ("acceptance_criteria", ["C999"]), ("requirements", [])):
            decision = self.decision(); decision["next_task"][field] = value
            before = copy.deepcopy(self.state)
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.apply_result(self.state, "astra_plan", decision, {}, self.root, self.run)
            self.assertEqual(before, self.state)

    def test_rework_dispatches_correction_and_records_the_decision(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        previous = self.state["current_task"]["id"]
        self.validation()
        self.state["validation"]["verdict"] = "FAIL"
        self.state["validation"]["criterion_results"][0]["status"] = "FAIL"
        correction = self.decision("REWORK")
        correction["next_objective"] = "Reject empty names"
        report = self.run / 'correction.json'
        report.write_text(json.dumps(correction))
        record = {"output": str(report), "source_revision": s.snapshot(self.root)['revision']}
        runner.apply_result(self.state, "astra_review", correction, record, self.root, self.run)
        self.assertEqual("astra_resolve", self.state["next_stage"])
        self.assertEqual(previous, self.state["current_task"]["id"])
        correction['diagnosis'] = 'The empty-name path does not reject invalid input'
        runner.apply_result(self.state, "astra_resolve", correction, record, self.root, self.run)
        self.assertEqual("terra", self.state["next_stage"])
        self.assertNotEqual(previous, self.state["current_task"]["id"])
        self.assertEqual("Reject empty names", self.state["current_task"]["objective"])
        self.assertEqual("REWORK", self.state["last_decision"]["report"]["status"])
        self.assertEqual(previous, self.state["task_archive"][0]["id"])
        with self.assertRaisesRegex(s.Paused, "another implementation task"):
            g.execution_guard(self.state, {**envelope(self.state), "task_id": previous})

    def test_astra_rework_survives_passing_sol_evidence(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        self.validation()
        correction = self.decision("REWORK")
        correction["acceptance_criteria"][0]["status"] = "unverified"
        correction["next_objective"] = "Fix the visual gap found by the Plan Reviewer"
        report = self.run / "visual-gap-review.json"
        report.write_text(json.dumps(correction))
        record = {"output": str(report), "source_revision": s.snapshot(self.root)["revision"]}
        runner.apply_result(self.state, "astra_review", correction, record, self.root, self.run)
        self.assertEqual("astra_resolve", self.state["next_stage"])
        self.assertEqual("RUNNING", self.state["status"])
        self.assertEqual("unverified", self.state["acceptance_criteria"][0]["status"])

    def test_continue_does_not_invent_verified_astra_criteria(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        self.validation()
        decision = self.decision()
        decision["acceptance_criteria"][0]["status"] = "unverified"
        runner.apply_result(self.state, "astra_review", decision, {"output": "review"}, self.root, self.run)
        self.assertEqual("terra", self.state["next_stage"])
        self.assertEqual("unverified", self.state["acceptance_criteria"][0]["status"])

    def test_continue_can_dispatch_revalidation_without_an_implementation(self):
        self.approve()
        decision = self.decision()
        decision["next_task"]["kind"] = "validate"
        runner.apply_result(self.state, "astra_review", decision, {"output": "validate"}, self.root, self.run)
        self.assertEqual("sol", self.state["next_stage"])
        self.assertEqual("validate", self.state["current_task"]["kind"])

    def test_completion_requires_final_end_to_end_evidence(self):
        self.approve(); current = self.validation()
        for flow in ({}, {"status": "NOT_VERIFIED", "summary": "Not checked", "evidence_refs": []},
                     {"status": "PASS", "summary": "Claim only", "evidence_refs": []}):
            self.state["validation"]["end_to_end_result"] = flow
            self.assertFalse(completion_gate.completion_ready(self.state, self.decision("COMPLETE"), current))

    def test_end_to_end_evidence_cannot_cite_fabricated_events(self):
        self.approve(); self.validation()
        value = copy.deepcopy(self.state["validation"])
        value["end_to_end_result"]["evidence_refs"] = ["event:invented"]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "missing executed event"):
            runner.apply_result(self.state, "sol", value, {"events": str(self.run / "sol.jsonl")}, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_technical_flow_proof_cannot_cite_fabricated_events(self):
        self.approve(); self.validation()
        value = copy.deepcopy(self.state["validation"])
        value["end_to_end_result"]["technical_result"] = {
            "status": "PASS", "summary": "Executed technical steps", "evidence_refs": ["event:invented"]}
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "missing executed event"):
            runner.apply_result(self.state, "sol", value, {"events": str(self.run / "sol.jsonl")}, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_passing_flow_cannot_hide_an_explicit_technical_gap(self):
        self.approve(); self.validation()
        for flow_status, status in (("PASS", "FAIL"), ("PASS", "NOT_VERIFIED"), ("NOT_VERIFIED", "FAIL")):
            with self.subTest(flow_status=flow_status, status=status):
                value = copy.deepcopy(self.state["validation"])
                value["end_to_end_result"]["status"] = flow_status
                value["end_to_end_result"]["technical_result"] = {
                    "status": status, "summary": "Installation unfinished", "evidence_refs": ["event:check"]}
                with self.assertRaisesRegex(ValueError, "End-to-end result conflicts"):
                    runner.apply_result(self.state, "sol", value, {"events": str(self.run / "sol.jsonl")}, self.root, self.run)

    def test_technical_flow_evidence_is_pinned_and_changes_block_completion(self):
        self.approve(); current = self.validation()
        artifact = self.run / "technical-flow.txt"
        artifact.write_text("Executed all technical flow steps")
        value = copy.deepcopy(self.state["validation"])
        value["end_to_end_result"]["technical_result"] = {
            "status": "PASS", "summary": "Executed technical steps", "evidence_refs": [str(artifact)]}
        record = {"events": str(self.run / "sol.jsonl"), "source_revision": current["revision"],
                  "output": str(self.run / "sol.jsonl")}
        runner.apply_result(self.state, "sol", value, record, self.root, self.run)
        self.assertTrue(completion_gate.completion_ready(self.state, self.decision("COMPLETE"), current))
        artifact.write_text("Changed technical-flow evidence")
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("COMPLETE"), current))

    def test_milestone_evidence_becomes_unverified_when_source_changes(self):
        self.approve(); current = self.validation()
        self.assertEqual("PASS", g.milestone_status(self.state, current)[0]["status"])
        (self.root / "greet.py").write_text("changed after validation")
        self.assertEqual("NOT_VERIFIED", g.milestone_status(self.state, s.snapshot(self.root))[0]["status"])

    def test_completion_rejects_sol_report_from_another_task(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        current = self.validation()
        self.assertTrue(completion_gate.completion_ready(self.state, self.decision("COMPLETE"), current))
        self.state["validation"]["task_id"] = "previous-task"
        self.assertFalse(completion_gate.completion_ready(self.state, self.decision("COMPLETE"), current))


if __name__ == "__main__":
    unittest.main()
