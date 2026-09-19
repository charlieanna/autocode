"""Goal gates in isolated Git workspaces. No real model or live run is used."""
import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_support as s
import autocode_goals as g
from goal_fixtures import body, envelope


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {"version": 2, "workspace": str(self.root), "task": "Make a greeting tool", "status": "RUNNING",
            "iteration": 1, "sessions": {}, "stages": [], "history": [], "next_stage": "terra", "acceptance_criteria": [],
            "settings": {"roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": self.local, "headroom": {"enabled": False}, "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "max_reported_tokens": None, "no_progress_batches": 3}}}
        g.migrate(self.state)

    def draft(self, **kwargs):
        g.install_draft(self.state, body(**kwargs), origin="test")

    def approve(self, **kwargs):
        self.draft(**kwargs)
        g.present(self.state)
        g.approve(self.state, g.token(self.state["goal_contract"]))

    def decision(self, status="CONTINUE"):
        criteria = [{**c, "status": "verified", "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria, "next_objective": "Implement greeting",
                "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet valid names; reject empty names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Execute both CLI cases"]},
                "agreed_limitations": [],
                "blocker": "", "evidence": ["event:check"], "plan": ["Greeting and checks"], "affected_paths": ["greet.py"]}

    def validation(self):
        evidence = self.run / "sol.jsonl"
        evidence.write_text(json.dumps({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
            "command": "python3 -m unittest", "exit_code": 0, "aggregated_output": "PASS"}}))
        current = s.snapshot(self.root)
        value = {**envelope(self.state), "verdict": "PASS", "findings": [], "unverified_criteria": [],
                 "checks_run": ["python3 -m unittest"], "checks": [{"command": "python3 -m unittest", "exit_code": 0,
                    "evidence_ref": "event:check"}], "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}]}
        value["end_to_end_result"] = {"status": "PASS", "summary": "Both CLI flows checked", "evidence_refs": ["event:check"]}
        record = {"events": str(evidence), "source_revision": current["revision"], "output": str(evidence)}
        runner.apply_result(self.state, "sol", value, record, self.root, self.run)
        return current

    def invoke(self, *args, role=None):
        s.atomic_json(self.run / "state.json", self.state)
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run), *args]
        with patch.object(sys, "argv", argv), patch.object(s, "assert_no_legacy_process"), \
             patch.object(s, "local_settings", return_value=self.local), \
             patch.object(runner, "run_role", side_effect=role or AssertionError("No agent may launch")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = runner.main()
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
        prompt, _ = s.context_packet(self.state, "astra_discovery", self.run / "state.json")
        self.assertIn('"Q1"', prompt)
        self.assertIn('"text": "CLI"', prompt)
        with self.assertRaisesRegex(ValueError, "already answered"):
            self.draft(questions=True)

    def test_answer_is_never_approval_and_resume_does_not_bypass_remaining_question(self):
        draft = body(questions=True)
        draft["open_blocking_questions"].append({**draft["open_blocking_questions"][0], "id": "Q2"})
        g.install_draft(self.state, draft, origin="test")
        self.invoke("--answer", "Q1=CLI")
        self.assertFalse(g.approved(self.state))
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.assertEqual(["Q2"], [q["id"] for q in self.state["pending_questions"]])

    def test_approval_requires_displayed_exact_revision(self):
        self.draft()
        selected = g.token(self.state["goal_contract"])
        with self.assertRaises(ValueError): g.approve(self.state, selected)
        g.present(self.state)
        g.approve(self.state, selected)
        self.assertTrue(g.approved(self.state))
        self.draft()
        g.present(self.state)
        with self.assertRaises(ValueError): g.approve(self.state, selected)
        self.assertFalse(g.approved(self.state))

    def test_edit_invalidates_approval_validation_and_human_review(self):
        self.approve(human=True)
        current = self.validation()
        g.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        old_token = g.token(self.state["goal_contract"])
        revised = body(human=True); revised["required_behaviors"].append("Support Unicode names")
        g.install_draft(self.state, revised, origin="user_edit")
        self.assertNotEqual(old_token, g.token(self.state["goal_contract"]))
        self.assertNotIn("validation", self.state)
        self.assertEqual({}, self.state["human_reviews"])
        self.assertTrue(self.state["validation_archive"])
        self.assertIn("Support Unicode names", g.render(self.state))
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
                self.approve()
                value = {**envelope(self.state), "summary": "Useful partial work", "user_request": {
                    "kind": kind, "discovered": "Need an external service", "impact": "Current scope prohibits it",
                    "decision_needed": "Choose local storage or authorize the service", "options": ["Local: no service", "Service: credentials needed"],
                    "proposed_delta": "Add service permission"}}
                runner.apply_result(self.state, "terra", value, {"output": "saved", "changed_files": ["greet.py"]}, self.root, self.run)
                self.assertEqual("astra_review", self.state["next_stage"])
                self.assertEqual("RUNNING", self.state["status"])
                self.assertEqual("Useful partial work", self.state["implementation"]["summary"])
                decision = {**self.decision("BLOCKED"), "user_request": value["user_request"]}
                runner.apply_result(self.state, "astra_review", decision, {"output": "blocked"}, self.root, self.run)
                self.assertEqual("WAITING_FOR_USER", self.state["phase"])
                self.assertEqual(2, self.invoke("--resume-paused"))

    def test_goal_change_answer_requires_new_revision_and_approval(self):
        self.approve()
        before = g.token(self.state["goal_contract"])
        g.wait_for_user(self.state, {"kind": "goal_change", "decision_needed": "Allow Unicode?", "impact": "Changes scope",
                                   "discovered": "Names include non-ASCII", "options": ["Yes", "No"], "proposed_delta": "Accept Unicode"})
        g.answer(self.state, self.state["pending_questions"][0]["id"], "Yes")
        self.assertFalse(g.approved(self.state))
        revised = body(); revised["required_behaviors"].append("Accept Unicode")
        g.install_draft(self.state, revised, origin="astra_discovery")
        self.assertNotEqual(before, g.token(self.state["goal_contract"]))
        with self.assertRaises(ValueError): g.approve(self.state, before)

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
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        g.present(self.state)
        g.approve_review(self.state, "C1", g.review_token(self.state), current)
        self.assertTrue(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
        for status in ["FAIL", "UNVERIFIED", "SKIPPED", "AWAITING_USER"]:
            saved = copy.deepcopy(self.state)
            self.state["validation"]["criterion_results"][0]["status"] = status
            self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))
            self.state = saved
        (self.root / "greet.py").write_text("changed")
        with self.assertRaises(ValueError):
            g.approve_review(self.state, "C1", g.review_token(self.state), s.snapshot(self.root))
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), s.snapshot(self.root)))

    def test_medium_blocking_finding_blocks_even_with_tests_passing(self):
        self.approve(); current = self.validation()
        self.state["validation"]["findings"] = [{"severity": "medium", "blocking": True, "finding": "Required behavior missing"}]
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))

    def test_missing_criterion_cannot_hide_behind_green_suite(self):
        self.approve(); current = self.validation()
        self.state["validation"]["criterion_results"] = []
        self.assertFalse(s.completion_ready(self.state, self.decision("TASK_COMPLETE"), current))

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
        g.present(self.state)
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
        with patch.object(s, "workspace_lock", concurrent_update):
            self.assertEqual(0, self.invoke("--show-goal"))
        self.assertIn({"kind": "concurrent-user-event"}, self.state["user_events"])

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
        with self.assertRaises(ValueError): g.install_draft(self.state, proposed, origin="agent")
        self.draft(questions=True)
        g.answer(self.state, "Q1", "accept default", delegated=True)
        proposed["delegated_decisions"][0]["answer_id"] = "Q1"
        g.install_draft(self.state, proposed, origin="agent")
        self.assertFalse(g.approved(self.state))

    def test_inferred_assumption_must_not_cite_an_answer_id(self):
        proposed = body()
        proposed["accepted_assumptions"] = [{"text": "Plan is the baseline", "basis": "original_request", "answer_id": "original-request"}]
        with self.assertRaises(ValueError): g.install_draft(self.state, proposed, origin="agent")
        proposed["accepted_assumptions"][0]["answer_id"] = ""
        g.install_draft(self.state, proposed, origin="agent")
        self.assertFalse(g.approved(self.state))

    def test_migration_retains_work_sessions_limits_and_does_not_approve(self):
        legacy = copy.deepcopy(self.state)
        legacy.update(version=2, next_stage="sol", sessions={"terra": "existing-session"},
                      implementation={"summary": "completed batch"}, changed_files=["greet.py"], iteration=4)
        legacy["validation"] = {"verdict": "PASS"}
        legacy["stages"] = [{"output": "saved-terra.json"}]
        legacy["answers"] = {"saved-question": {"text": "Known answer", "kind": "answer"}}
        g.migrate(legacy)
        self.assertEqual("existing-session", legacy["sessions"]["terra"])
        self.assertEqual("completed batch", legacy["implementation"]["summary"])
        self.assertEqual(4, legacy["iteration"])
        self.assertEqual("sol", legacy["pre_goal_checkpoint"]["next_stage"])
        self.assertEqual("astra_discovery", legacy["next_stage"])
        self.assertFalse(g.approved(legacy))
        self.assertEqual("saved-terra.json", legacy["stages"][0]["output"])
        self.assertTrue(legacy["validation_archive"])
        self.assertEqual("Known answer", legacy["answers"]["saved-question"]["text"])

    def test_active_or_uncertain_migration_refuses_without_discarding_work(self):
        for field in ["active_stage", "uncertain_artifacts"]:
            legacy = {**self.state, "version": 2, field: "still pending"}
            before = copy.deepcopy(legacy)
            with self.assertRaises(s.Paused): g.migrate(legacy)
            self.assertEqual(before, legacy)

    def test_limits_pause_and_cannot_complete(self):
        self.approve()
        for name, value, expected in [("iteration_ceiling", 0, "PAUSED_ITERATION_LIMIT"),
                                      ("max_seconds", 1, "PAUSED_TIME_LIMIT"),
                                      ("max_reported_tokens", 1, "PAUSED_BUDGET"),
                                      ("no_progress_batches", 1, "PAUSED_NO_PROGRESS")]:
            with self.subTest(name=name):
                saved = copy.deepcopy(self.state)
                self.state["settings"]["limits"][name] = value
                self.state.update(active_seconds=1, no_progress_batches=1,
                                  stages=[{"metrics": {"provider_tokens": {"input_tokens": 1, "output_tokens": 1}}}])
                self.assertEqual(2, self.invoke())
                self.assertEqual(expected, self.state["status"])
                self.assertEqual("PAUSED_OR_BLOCKED", self.state["phase"])
                self.state = saved

    def test_cli_approve_saves_ready_without_launching(self):
        self.draft()
        self.invoke("--show-goal")
        self.assertEqual(0, self.invoke("--approve-goal", g.token(self.state["goal_contract"])))
        self.assertEqual("READY_TO_EXECUTE", self.state["phase"])

    def test_runner_guard_rejects_unapproved_stage_before_subprocess(self):
        self.draft()
        self.state.update(next_stage="terra", status="RUNNING", phase="EXECUTING")
        self.assertEqual(2, self.invoke("--resume-paused"))
        self.assertEqual("PAUSED_GOAL_UNAPPROVED", self.state["status"])

    def test_user_cannot_combine_answer_and_approval(self):
        self.draft(questions=True)
        with self.assertRaises(SystemExit): self.invoke("--answer", "Q1=CLI", "--approve-goal", "anything")

    def test_new_brief_requires_flow_approach_and_milestones_covering_criteria(self):
        for field in g.BRIEF_FIELDS:
            draft = body(); draft[field] = []
            with self.subTest(field=field), self.assertRaises(ValueError):
                g.install_draft(self.state, draft, origin="test")
        draft = body(); draft["milestones"][0]["acceptance_criteria"] = ["invented"]
        with self.assertRaises(ValueError): g.install_draft(self.state, draft, origin="test")
        draft = body(); draft["acceptance_criteria"].append({**draft["acceptance_criteria"][0], "id": "C2"})
        with self.assertRaises(ValueError): g.install_draft(self.state, draft, origin="test")

    def test_existing_v3_brief_can_be_approved_without_rewriting_its_contract(self):
        self.draft()
        contract = self.state["goal_contract"]
        for field in g.BRIEF_FIELDS:
            del contract["body"][field]
        contract["hash"] = s.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        original = copy.deepcopy(contract["body"])
        g.present(self.state)
        g.approve(self.state, g.token(contract))
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
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.state["status"])
        self.assertFalse(g.approved(self.state))

    def test_brief_feedback_is_saved_as_input_and_requires_a_new_approval(self):
        self.draft()
        g.present(self.state)
        old = g.token(self.state["goal_contract"])
        self.assertEqual(0, self.invoke("--feedback", "Keep Unicode names in the first milestone"))
        self.assertEqual("astra_discovery", self.state["next_stage"])
        self.assertFalse(g.approved(self.state))
        with self.assertRaises(ValueError): g.approve(self.state, old)
        prompt, _ = s.context_packet(self.state, "astra_discovery", self.run / "state.json")
        self.assertIn("Keep Unicode names", prompt)
        event = self.state["brief_feedback"][0]
        draft = body()
        draft["accepted_assumptions"].append({"text": event["text"], "basis": "user_feedback", "answer_id": event["id"]})
        g.install_draft(self.state, draft, origin="astra_discovery")
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
        g.install_draft(self.state, draft, origin="test")
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
            prompt, _ = s.context_packet(self.state, stage, self.run / "state.json")
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
        runner.apply_result(self.state, "astra_review", correction, {"output": "correction"}, self.root, self.run)
        self.assertEqual("terra", self.state["next_stage"])
        self.assertNotEqual(previous, self.state["current_task"]["id"])
        self.assertEqual("Reject empty names", self.state["current_task"]["objective"])
        self.assertEqual("REWORK", self.state["last_decision"]["report"]["status"])
        self.assertEqual(previous, self.state["task_archive"][0]["id"])
        with self.assertRaisesRegex(s.Paused, "another implementation task"):
            g.execution_guard(self.state, {**envelope(self.state), "task_id": previous})

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
            self.assertFalse(s.completion_ready(self.state, self.decision("COMPLETE"), current))

    def test_end_to_end_evidence_cannot_cite_fabricated_events(self):
        self.approve(); self.validation()
        value = copy.deepcopy(self.state["validation"])
        value["end_to_end_result"]["evidence_refs"] = ["event:invented"]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "missing executed event"):
            runner.apply_result(self.state, "sol", value, {"events": str(self.run / "sol.jsonl")}, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_milestone_evidence_becomes_unverified_when_source_changes(self):
        self.approve(); current = self.validation()
        self.assertEqual("PASS", g.milestone_status(self.state, current)[0]["status"])
        (self.root / "greet.py").write_text("changed after validation")
        self.assertEqual("NOT_VERIFIED", g.milestone_status(self.state, s.snapshot(self.root))[0]["status"])

    def test_completion_rejects_sol_report_from_another_task(self):
        self.approve()
        runner.apply_result(self.state, "astra_plan", self.decision(), {"output": "plan"}, self.root, self.run)
        current = self.validation()
        self.assertTrue(s.completion_ready(self.state, self.decision("COMPLETE"), current))
        self.state["validation"]["task_id"] = "previous-task"
        self.assertFalse(s.completion_ready(self.state, self.decision("COMPLETE"), current))


if __name__ == "__main__":
    unittest.main()
