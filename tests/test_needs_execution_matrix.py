"""Execute the documented needs contract through TaskRun and the real CLI, without models."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_args as cli_args
import autocode_controller_owner as controller_owner
import autocode_dependency as dependency
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_job_failure as job_failure
import autocode_job_source as job_source
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_support as support
import autocode_taskrun as taskrun
import goal_fixtures

from .test_needs_contract import NEED_BUILDERS, documented_kinds, emitted_kinds

KIND_CASES = {
    "approve_plan": "test_goal_approval_executes_only_for_the_displayed_plan",
    "answer": "test_clarification_answer_releases_the_question_without_approving",
    "review": "test_human_review_accepts_only_the_current_validated_artifact",
    "planning_budget": "test_planning_budget_feedback_releases_only_a_new_draft",
    "recover_source": "test_recover_source_names_the_required_inspection",
    "retry_job": "test_exact_job_retry_checks_source_and_current_token",
    "resume": "test_resume_reaches_a_natural_question_boundary",
    "dependency": "test_dependency_wait_releases_only_for_bound_verified_delivery",
    "continue": "test_continue_reaches_a_natural_question_boundary",
}
SCOPE_CASES = {
    "clarification": KIND_CASES["answer"],
    "goal_approval": KIND_CASES["approve_plan"],
    "permission": "test_scoped_permission_preserves_approval_and_accepts_only_offered_answers",
    "goal_change": "test_goal_change_answer_requires_fresh_planning_and_approval",
    "human_review": KIND_CASES["review"],
    "blocker": "test_blocker_information_cannot_change_the_approved_contract",
    "operational_exhaustion": "test_operational_information_holds_existing_allowances",
    "intake": "test_intake_answer_records_intent_without_approving_execution",
}


class NeedsExecutionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_directory = tempfile.TemporaryDirectory(prefix="needs-matrix-git-")
        cls.seed = Path(cls.seed_directory.name)
        goal_fixtures.seed_greeting_workspace(cls.seed)
        subprocess.run(["git", "init", "-q", str(cls.seed)], check=True)
        subprocess.run(["git", "-C", str(cls.seed), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(cls.seed),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.seed_directory.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="needs-matrix-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        shutil.copytree(self.seed, self.workspace)
        self.directory = self.workspace / ".autocode/runs/matrix"
        self.directory.mkdir(parents=True)
        self.state = {
            "version": 2,
            "workspace": str(self.workspace),
            "run_dir": str(self.directory),
            "task": "Make a greeting tool",
            "status": "RUNNING",
            "iteration": 1,
            "phase": "EXECUTING",
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "terra",
            "acceptance_criteria": [],
            "settings": {
                "engine": "codex",
                "roles": {role: {"model": role, "reasoning_effort": "high"} for role in ("astra", "terra", "sol")},
                "transport_identity": {"auth_mode": "fixture"},
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }
        self.env = {"AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, self.env))
        self.stack.enter_context(patch.object(support, "local_settings", return_value={"auth_mode": "fixture"}))
        self.stack.enter_context(patch.object(support, "assert_no_legacy_process"))
        self.provider = self.stack.enter_context(
            patch.object(runner, "run_role", side_effect=AssertionError("No model may launch"))
        )
        self.stack.enter_context(patch.object(taskrun, "run_process", self.cli))
        lifecycle.migrate(self.state)
        support.atomic_json(self.directory / "state.json", self.state)
        arguments, _ = cli_args.parse(
            None,
            ["--workspace", str(self.workspace), "--run-dir", str(self.directory), "--no-chat"],
            runner.DEFAULT_ROLE_MODELS,
        )
        self.state["settings"] = runner.configure(arguments, self.state)
        self.run = taskrun.TaskRun(self.workspace, self.directory, env=self.env, cwd=self.root)

    def cli(self, argv, *, env=None, cwd=None, timeout=None, advancing=False):
        """Replace process startup only; run the actual parser, actions, persistence and status command."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["autocode", *argv[2:]]),
            patch.dict(os.environ, env or {}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = runner.main()
            except SystemExit as error:
                code = error.code
        return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())

    def save(self):
        runner.write_json(self.directory / "state.json", self.state)

    def shown(self, kind, *, scope=None):
        """The real CLI status and the additive status projection must name the same need."""
        view = self.run.status()
        saved = support.read(self.directory / "state.json")
        projected = run_view.view(saved, run_dir=self.directory, workspace=self.workspace)
        self.assertEqual(kind, view["needs"]["kind"])
        self.assertEqual(projected["needs"], view["needs"])
        if scope is not None:
            current = human.current(saved)
            self.assertIsNotNone(current)
            self.assertEqual(scope, current["scope"])
            if kind == "answer":
                self.assertEqual(scope, view["needs"]["resolver_scope"])
        self.assertTrue(view["progress"]["needs_you"])
        return view

    def protected(self, view):
        return {key: copy.deepcopy(view.get(key)) for key in ("approved_contract", "usage", "cost", "evidence")}

    def refuse(self, action, before, reason):
        with self.assertRaisesRegex(taskrun.TaskRunError, reason):
            action()
        after = self.run.status()
        self.assertEqual(before["needs"], after["needs"])
        self.assertEqual(self.protected(before), self.protected(after))
        self.provider.assert_not_called()

    def draft(self, *, questions=False, human_review=False, approve=False):
        lifecycle.install_draft(
            self.state, goal_fixtures.body(questions=questions, human=human_review), origin="fixture"
        )
        self.save()
        lifecycle.present(self.state, self.directory)
        self.save()
        if approve:
            lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
            self.state.update(next_stage="terra", phase="EXECUTING")
            self.save()

    def request(self, scope, *, options=None):
        request = {
            "kind": scope,
            "discovered": "An operator decision is needed",
            "impact": "Affects the planned work",
            "decision_needed": "Keep the current scope or authorize the proposed decision?",
            "options": ["Keep the current scope", "Authorize the proposed decision"] if options is None else options,
            "proposed_delta": "Add a web interface"
            if scope == "goal_change"
            else "No goal, scope, criterion or behavior change",
        }
        human.queue(self.state, scope, {"stage": "terra"}, request=request, next_stage="terra")
        self.save()

    def provider_question(self, **kwargs):
        """Deterministic read-only model I/O; the actual controller installs and publishes its draft."""
        output = self.directory / "planner-fixture.json"
        value = {"contract": goal_fixtures.body(questions=True), "summary": "The interface needs clarification"}
        support.atomic_json(output, value)
        return value, {"output": str(output), "duration_seconds": 0.01}

    def ready(self):
        self.save()
        # Admit the fixture as a deliberately ready controller checkpoint, not an owner-lost run.
        with controller_owner.Owner(self.state, self.directory, runner.write_json) as owner:
            owner.result(0, ready=True)

    def test_documented_kinds_and_resolver_scopes_require_executed_rows(self):
        emitted = set().union(*(emitted_kinds(module, function) for module, function in NEED_BUILDERS))
        self.assertEqual(documented_kinds(), set(KIND_CASES))
        self.assertEqual(emitted, set(KIND_CASES))
        self.assertEqual(set(human.SCOPES), set(SCOPE_CASES))
        lines = (Path(__file__).resolve().parents[1] / "docs/task-run.md").read_text().splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("| Resolver scope |"))
        scopes = set()
        for line in lines[start + 2 :]:
            if not line.startswith("| `"):
                break
            scopes.add(line.split("`")[1])
        self.assertEqual(set(SCOPE_CASES), scopes)
        for name in {*KIND_CASES.values(), *SCOPE_CASES.values()}:
            self.assertTrue(callable(getattr(type(self), name, None)), name)

    def test_goal_approval_executes_only_for_the_displayed_plan(self):
        self.draft()
        before = self.shown("approve_plan", scope="goal_approval")
        self.assertEqual("plan approval needed", before["progress"]["needs_you"])
        self.refuse(lambda: self.run.approve_plan("stale-plan-token"), before, "token")
        after = self.run.approve_plan(before["needs"]["token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertIsNotNone(after["approved_contract"])
        self.assertEqual(before["displayed_plan"]["hash"], after["approved_contract"]["hash"])
        self.assertEqual(before["evidence"], after["evidence"])
        self.provider.assert_not_called()

    def test_clarification_answer_releases_the_question_without_approving(self):
        self.draft(questions=True)
        before = self.shown("answer", scope="clarification")
        self.assertEqual("1 question to answer", before["progress"]["needs_you"])
        need = before["needs"]
        question = need["questions"][0]
        self.refuse(lambda: self.run.answer(question["id"], "CLI", resolver_token="stale"), before, "token|request")
        after = self.run.answer(question["id"], "CLI", resolver_token=need["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual("astra_discovery", after["next_stage"])
        with self.assertRaises(taskrun.TaskRunError):
            self.run.answer(question["id"], "CLI", resolver_token=need["resolver_token"])
        self.provider.assert_not_called()

    def test_scoped_permission_preserves_approval_and_accepts_only_offered_answers(self):
        self.draft(approve=True)
        self.request("permission")
        before = self.shown("answer", scope="permission")
        need = before["needs"]
        question = need["questions"][0]
        self.refuse(
            lambda: self.run.answer(question["id"], "An unoffered answer", resolver_token=need["resolver_token"]),
            before,
            "option|offered",
        )
        self.refuse(lambda: self.run.approve_plan(need["resolver_token"]), before, "plan request|approval")
        after = self.run.answer(question["id"], question["options"][0], resolver_token=need["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual(self.protected(before), self.protected(after))
        self.assertEqual("terra", after["next_stage"])
        self.provider.assert_not_called()

    def test_clarification_accepts_the_documented_proposed_default(self):
        self.draft(questions=True)
        before = self.shown("answer", scope="clarification")
        need = before["needs"]
        question = need["questions"][0]
        self.assertNotIn(question["proposed_default"], question["options"])
        after = self.run.answer(question["id"], question["proposed_default"], resolver_token=need["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertIsNone(after.get("approved_contract"))
        self.provider.assert_not_called()

    def test_permission_without_options_accepts_a_free_text_response(self):
        self.draft(approve=True)
        self.request("permission", options=[])
        before = self.shown("answer", scope="permission")
        need = before["needs"]
        self.assertEqual([], need["questions"][0]["options"])
        after = self.run.answer(
            need["questions"][0]["id"], "Allow a local comment only", resolver_token=need["resolver_token"]
        )
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual(self.protected(before), self.protected(after))
        self.provider.assert_not_called()

    def test_structured_permission_keeps_exact_choices_and_client_timeout_overrides(self):
        self.draft(approve=True)
        self.state["settings"]["limits"].update(tool_timeout_seconds=10, stage_timeout_seconds=20)
        self.run.options = ("--max-tool-seconds", "10", "--max-stage-seconds", "20")
        rows = [
            {"option": "Raise only the tool timeout", "changes": {"settings.limits.tool_timeout_seconds": 30}},
            {"option": "Decline", "changes": {}},
        ]
        lifecycle.wait_for_user(
            self.state,
            {
                "kind": "permission",
                "discovered": "A command needs more time",
                "impact": "One timeout would change",
                "decision_needed": "Increase this exact timeout or decline?",
                "options": [row["option"] for row in rows],
                "proposed_delta": json.dumps({"operational_options": rows}),
            },
            origin={"stage": "terra"},
            next_stage="terra",
        )
        self.save()
        before = self.shown("answer", scope="permission")
        need = before["needs"]
        question = need["questions"][0]
        self.assertEqual(rows, need["operational_options"])
        original_options = self.run.options
        self.refuse(
            lambda: self.run.answer(
                question["id"], "  Raise only the tool timeout  ", resolver_token=need["resolver_token"]
            ),
            before,
            "option|choice|offered",
        )
        self.assertEqual(original_options, self.run.options)
        after = self.run.answer(question["id"], rows[0]["option"], resolver_token=need["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual(before["approved_contract"], after["approved_contract"])
        self.assertEqual(("--max-tool-seconds", "30", "--max-stage-seconds", "20"), self.run.options)
        self.provider.assert_not_called()

    def test_immutable_component_binding_requires_a_new_run(self):
        self.state.update(
            status="PAUSED_COMPONENT_PLAN",
            stop_reason="Caller root and generated ownership disagree",
            task="Implement the gateway component of a larger system: HTTP service. Own only the directory components/gateway/; do not create or edit any file outside it.",
            next_stage="astra_discovery",
        )
        self.state["settings"].setdefault("regression", {})["test_root"] = "components/backend"
        self.save()
        before = self.shown("resume")
        self.assertTrue(before["needs"]["new_run_required"])
        self.assertEqual("fresh_run", before["needs"]["action"])
        self.assertIn("fresh component run", before["progress"]["needs_you"])
        self.refuse(
            lambda: self.run.feedback("Change the plan to match the old caller root"), before, "immutable|fresh"
        )
        after = self.run.resume_paused()
        self.assertEqual(before["needs"], after["needs"])
        self.assertEqual(self.protected(before), self.protected(after))
        self.provider.assert_not_called()

    def test_goal_change_answer_requires_fresh_planning_and_approval(self):
        self.draft(approve=True)
        self.request("goal_change")
        before = self.shown("answer", scope="goal_change")
        need = before["needs"]
        question = need["questions"][0]
        after = self.run.answer(question["id"], question["options"][1], resolver_token=need["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual("astra_discovery", after["next_stage"])
        self.assertEqual(before["evidence"], after["evidence"])
        self.provider.assert_not_called()

    def test_intake_answer_records_intent_without_approving_execution(self):
        self.state.pop("goal_contract", None)
        self.state.update(
            conversation_id="matrix-intake", intake_input_hash="human-message", next_stage="astra_discovery"
        )
        question = goal_fixtures.body(questions=True)["open_blocking_questions"][0]
        human.queue(
            self.state, "intake", {"stage": "resolver_intake"}, questions=[question], next_stage="astra_discovery"
        )
        self.save()
        before = self.shown("answer", scope="intake")
        after = self.run.answer(question["id"], "CLI", resolver_token=before["needs"]["resolver_token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertIsNone(after.get("displayed_plan"))
        self.assertEqual("astra_discovery", after["next_stage"])
        self.provider.assert_not_called()

    def test_planning_budget_feedback_releases_only_a_new_draft(self):
        self.draft()
        self.state.update(status="PAUSED_PLANNING_BUDGET", stop_reason="Plan review allowance used up")
        self.state.pop(human.PUBLIC, None)
        self.save()
        before = self.shown("planning_budget")
        self.assertEqual("plan review budget used up", before["progress"]["needs_you"])
        after = self.run.feedback("Keep the scope; simplify the plan before another review")
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual("RUNNING", after["status"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual("astra_discovery", after["next_stage"])
        self.provider.assert_not_called()

    def test_continue_reaches_a_natural_question_boundary(self):
        self.state.update(next_stage="astra_discovery", phase="DISCOVERING")
        self.ready()
        before = self.shown("continue")
        self.assertEqual("nothing needed from you", before["progress"]["needs_you"])
        self.provider.side_effect = self.provider_question
        after = self.run.advance()
        self.assertEqual("WAITING_FOR_USER", after["status"])
        self.assertEqual("answer", after["needs"]["kind"])
        self.assertEqual("clarification", after["needs"]["resolver_scope"])
        self.assertEqual("1 question to answer", after["progress"]["needs_you"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual(1, self.provider.call_count)

    def test_resume_reaches_a_natural_question_boundary(self):
        self.state.update(
            status="PAUSED_REQUESTED", stop_reason="Operator inspected a requested pause", next_stage="astra_discovery"
        )
        self.save()
        before = self.shown("resume")
        self.assertEqual("inspect the pause, then resume", before["progress"]["needs_you"])
        self.provider.side_effect = self.provider_question
        after = self.run.resume_paused()
        self.assertEqual("WAITING_FOR_USER", after["status"])
        self.assertEqual("answer", after["needs"]["kind"])
        self.assertEqual("clarification", after["needs"]["resolver_scope"])
        self.assertIsNone(after.get("approved_contract"))
        self.assertEqual(1, self.provider.call_count)

    def test_recover_source_names_the_required_inspection(self):
        self.state.update(status="PAUSED_JOB_FAILURE", stop_reason="The original source capture is unavailable")
        self.state["job_failure"] = {
            "reason": "The original source capture is unavailable",
            "stage": "answer_question",
            "attempt_id": "001/answer_question-01",
            "job_retry_token": "inspected-job-token",
            "archive": str(self.directory / "archive"),
            "source_identity": None,
            "write_diagnosis": {},
            "unrestored": [],
        }
        self.save()
        view = self.run.status()
        self.assertEqual("recover_source", view["needs"]["kind"])
        self.assertIsNone(view["needs"]["action"])
        self.assertIn("inspect", view["progress"]["needs_you"].lower())
        self.assertIn("new run", view["progress"]["needs_you"].lower())
        self.refuse(
            lambda: self.run.retry_job(view["needs"]["job_retry_token"]), view, "no saved original source identity"
        )
        after = self.run.resume_paused()
        self.assertEqual("recover_source", after["needs"]["kind"])
        self.assertEqual(self.protected(view), self.protected(after))
        self.provider.assert_not_called()

    def test_human_review_accepts_only_the_current_validated_artifact(self):
        self.draft(human_review=True, approve=True)
        evidence = self.directory / "validator.json"
        support.atomic_json(evidence, {"check": "greeting", "exit_code": 0})
        contract = self.state["goal_contract"]
        self.state["validation"] = {
            "verdict": "PASS",
            "source_revision": support.snapshot(self.workspace)["revision"],
            "contract_revision": contract["revision"],
            "contract_hash": contract["hash"],
            "criteria_revision": self.state["criteria_revision"],
            "evidence_hashes": {str(evidence): support.file_hash(evidence)},
            "checks": [{"command": "python -m unittest test_greeting", "exit_code": 0, "evidence_ref": str(evidence)}],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": [str(evidence)]}],
            "findings": [],
        }
        self.state["displayed_review"] = goals.review_token(self.state)
        human.queue(
            self.state,
            "human_review",
            {"stage": "astra_review"},
            request={"kind": "human_review", "criteria": ["C1"], "decision_needed": "Review the current greeting"},
            evidence={"review_token": self.state["displayed_review"]},
        )
        self.save()
        before = self.shown("review", scope="human_review")
        self.assertEqual("1 requirement to review", before["progress"]["needs_you"])
        self.refuse(lambda: self.run.approve_review("C1", "stale-artifact-token"), before, "current|displayed")
        original = evidence.read_bytes()
        evidence.write_bytes(original + b"\nchanged evidence\n")
        self.refuse(lambda: self.run.approve_review("C1", before["needs"]["token"]), before, "current|artifact")
        self.assertEqual(original + b"\nchanged evidence\n", evidence.read_bytes())
        evidence.write_bytes(original)
        after = self.run.approve_review("C1", before["needs"]["token"])
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual(before["approved_contract"], after["approved_contract"])
        self.assertEqual("astra_review", after["next_stage"])
        accepted = next(row for row in after["evidence"]["acceptance"] if row["id"] == "C1")
        self.assertTrue(accepted["human_reviewed"])
        self.provider.assert_not_called()

    def operational(self, scope):
        self.draft(approve=True)
        if scope == "operational_exhaustion":
            self.state.update(status="PAUSED_TIMEOUT_RECOVERY", automatic_recoveries_since_resume=3, active_seconds=17)
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner,
                    self.state,
                    self.directory,
                    support.Paused("PAUSED_TIMEOUT_RECOVERY", "Recovery allowance exhausted"),
                )
            )
        else:
            evidence = self.directory / "diagnosed-blocker.json"
            support.atomic_json(evidence, {"diagnosis": "The criterion conflicts with the approved scope"})
            self.state["stages"].append(
                {"stage": "astra_resolve", "output": str(evidence), "exit_code": 0, "changed_files": []}
            )
            human.queue(
                self.state,
                "blocker",
                {"stage": "astra_resolve"},
                request={
                    "kind": "blocker",
                    "decision_needed": "Provide corrective information or leave paused",
                    "impact": "The current criterion conflicts with the approved scope",
                    "options": ["Provide information", "Leave paused"],
                    "proposed_delta": "Change the approved criterion",
                },
                evidence={"diagnosis": "Contradictory criterion", "output": str(evidence)},
            )
        self.save()
        return self.shown("answer", scope=scope)

    def respond_information(self, scope, text):
        before = self.operational(scope)
        need = before["needs"]
        self.assertIn("--resolver-response", need["action"])
        question = need["questions"][0]
        self.refuse(
            lambda: self.run.answer(question["id"], question["options"][0], resolver_token=need["resolver_token"]),
            before,
            "resolver-response|operational",
        )
        self.refuse(
            lambda: self.run.respond_operational(need["resolver_request_id"], "stale", text), before, "token|request"
        )
        after = self.run.respond_operational(need["resolver_request_id"], need["resolver_token"], text)
        self.assertEqual("resume", after["needs"]["kind"])
        self.assertEqual(self.protected(before), self.protected(after))
        self.provider.assert_not_called()
        replay = self.run.respond_operational(need["resolver_request_id"], need["resolver_token"], text)
        self.assertEqual(after["needs"], replay["needs"])
        self.assertEqual(self.protected(after), self.protected(replay))
        self.provider.assert_not_called()
        return after

    def test_blocker_information_cannot_change_the_approved_contract(self):
        after = self.respond_information("blocker", "Remove this criterion and deliver only the old scope")
        self.assertIn("--edit-goal", after["needs"]["reason"])
        self.assertIn("--approve-goal", after["needs"]["reason"])

    def test_operational_information_holds_existing_allowances(self):
        after = self.respond_information("operational_exhaustion", "The environment was repaired; keep existing limits")
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", after["status"])
        self.assertEqual("pending", after["information_review"]["status"])
        self.assertIn("no execution, approval or additional allowance was authorized", after["stop_reason"])

    def test_exact_job_retry_checks_source_and_current_token(self):
        self.state.update(
            status="PAUSED_JOB_FAILURE",
            stop_reason="The Analyst stopped",
            next_stage="answer_question",
            workflow={"kind": "discuss", "then": "requirements_gather"},
        )
        self.state["job_failure"] = {
            "reason": "The Analyst stopped",
            "stage": "answer_question",
            "attempt_id": "001/answer_question-01",
            "job_retry_token": "jr:fixture",
            "archive": str(self.directory / "archive"),
            "source_identity": support.digest(job_source.identity(self.workspace)),
            "configuration": job_failure.configuration(self.state),
            "write_diagnosis": {},
            "unrestored": [],
        }
        self.save()
        before = self.shown("retry_job")
        self.assertEqual("inspect the failed job, then retry", before["progress"]["needs_you"])
        self.refuse(lambda: self.run.retry_job("stale-job-token"), before, "token")
        source = (self.workspace / "greet.py").read_bytes()
        edited = source + b"\n# Source changed after the failed job\n"
        (self.workspace / "greet.py").write_bytes(edited)
        self.refuse(lambda: self.run.retry_job(before["needs"]["job_retry_token"]), before, "Source changed|stale")
        self.assertEqual(edited, (self.workspace / "greet.py").read_bytes())
        (self.workspace / "greet.py").write_bytes(source)

        def answer(**kwargs):
            output = self.directory / "analyst-fixture.json"
            value = {
                "answer": "The greeting reads one name from argv",
                "evidence": [{"claim": "The CLI reads argv", "source": "greet.py", "example": "", "probe": ""}],
                "questions": [],
                "note_path": "",
                "note_content": "",
            }
            support.atomic_json(output, value)
            return value, {"output": str(output), "duration_seconds": 0.01, "changed_files": []}

        self.provider.side_effect = answer
        after = self.run.retry_job(before["needs"]["job_retry_token"])
        self.assertTrue(after["done"])
        self.assertIsNone(after["needs"])
        self.assertEqual("nothing needed from you", after["progress"]["needs_you"])
        self.assertEqual(source, (self.workspace / "greet.py").read_bytes())
        self.assertEqual(1, self.provider.call_count)

    def test_dependency_wait_releases_only_for_bound_verified_delivery(self):
        self.draft(approve=True)
        self.request("permission")
        permission = self.shown("answer", scope="permission")
        producer = self.root / "producer"
        shutil.copytree(self.seed, producer)
        producer_run = producer / ".autocode/runs/producer"
        producer_run.mkdir(parents=True)
        spec = {
            "producer_workspace": str(producer),
            "producer_run": str(producer_run),
            "question_id": permission["needs"]["questions"][0]["id"],
            "request_token": permission["needs"]["resolver_token"],
            "files": ["greet.py"],
            "destination": "evidence/delivery",
            "label": "Verified greeting prerequisite",
        }
        specification = self.root / "dependency.json"
        support.atomic_json(specification, spec)
        before = self.run.bind_dependency(specification)
        self.assertEqual("dependency", before["needs"]["kind"])
        self.assertEqual("nothing needed from you until the other run delivers", before["progress"]["needs_you"])
        self.assertIsNone(before["next_command"])
        held = self.run.advance()
        self.assertEqual(before["needs"], held["needs"])
        self.assertEqual(self.protected(before), self.protected(held))
        self.provider.assert_not_called()
        delivery = self.directory / spec["destination"]
        (delivery / "source").mkdir(parents=True)
        shutil.copy2(producer / "greet.py", delivery / "source/greet.py")
        contract = {"task_id": "producer-task", "revision": 1, "body": goal_fixtures.body()}
        contract.update(hash=support.digest(contract), approval_status="approved")
        snapshot = support.snapshot(producer)
        support.atomic_json(delivery / "contract.json", contract)
        support.atomic_json(delivery / "source-snapshot.json", snapshot)
        proofs = {}
        for role, report in (("validator", {"verdict": "PASS"}), ("reviewer", {"status": "COMPLETE"})):
            path = delivery / (role + ".json")
            support.atomic_json(path, {**report, "contract_hash": contract["hash"]})
            proofs[role] = {"path": path.name, "sha256": dependency.sha(path), "source_revision": snapshot["revision"]}
        manifest = delivery / "manifest.json"
        receipt = {
            "producer_workspace": str(producer),
            "producer_run": str(producer_run),
            "consumer_contract": permission["approved_contract"]["hash"],
            "producer_contract": contract["hash"],
            "files": {"greet.py": dependency.sha(delivery / "source/greet.py")},
            "verified_complete": True,
            "source_revision": snapshot["revision"],
            "proofs": proofs,
            "pins": {name: dependency.sha(delivery / name) for name in ("contract.json", "source-snapshot.json")},
        }
        support.atomic_json(manifest, {**receipt, "verified_complete": False})
        self.refuse(lambda: self.run.receive_dependency(manifest), before, "authorized dependency")
        support.atomic_json(manifest, receipt)
        after = self.run.receive_dependency(manifest)
        self.assertEqual("continue", after["needs"]["kind"])
        self.assertEqual(before["approved_contract"], after["approved_contract"])
        self.assertEqual("delivered", after["dependency"]["status"])
        self.assertEqual("terra", after["next_stage"])
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
