"""The task-run interface: the status view and the CLI client. See docs/task-run.md."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_run_actions as run_actions

HERE = Path(__file__).resolve().parents[1] / "tools"  # its fixtures stay beside the runtime
# The offline fixture provider plans and builds exactly this greeting task.
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
FIXTURE_OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


class RunViewTests(unittest.TestCase):
    def test_explicit_report_retry_bypasses_automatic_escalation(self):
        self.assertTrue(run_actions.explicit_recovery_requested(SimpleNamespace(retry_report='001/sol_report_repair-02')))
        self.assertFalse(run_actions.explicit_recovery_requested(SimpleNamespace(retry_report=None)))

    def test_contract_fields(self):
        self.assertEqual({"schema", "status", "done", "needs", "phase", "next_stage", "iteration", "stop_reason", "runner_check",
                          "current_task", "workflow", "workflow_source", "workflow_reason", "turn", "evidence",
                          "dependency", "usage", "request_context", "output_transport", "direct_rework_assignments"},
                         set(run_view.view({"status": "RUNNING"})))

    def test_evidence_is_empty_before_planning(self):
        self.assertEqual({"outcome": None, "base_commit": None, "acceptance": [], "findings": [],
                          "regression_proof": None, "test_cases": [], "check_replay": None},
                         run_view.evidence({"status": "RUNNING"}))

    def test_direct_rework_provenance_is_not_completion_proof_and_is_copied(self):
        receipt = {"source_task_id": "task-old", "assigned_task_id": "task-repair",
                   "evidence_hashes": {"review.json": "digest"}, "provenance": "completion_direct_assignment"}
        state = {"status": "RUNNING", "direct_rework_assignments": [receipt]}
        view = run_view.view(state)
        self.assertEqual([receipt], view["direct_rework_assignments"])
        self.assertFalse(view["done"])
        self.assertIsNone(view["evidence"]["check_replay"])
        view["direct_rework_assignments"][0]["evidence_hashes"]["review.json"] = "changed"
        self.assertEqual("digest", receipt["evidence_hashes"]["review.json"])
        self.assertEqual([], run_view.view({"status": "RUNNING"})["direct_rework_assignments"])

    def test_evidence_carries_the_english_test_cases_and_what_proves_them(self):
        case = {"id": "T1", "given": "a timeout", "when": "renew()", "then": "one mutation"}
        state = {"status": "TASK_COMPLETE", "investigation": {"outcome": "reproduced", "test_cases": [case]},
                 "regression_proof": {"verdict": "PASS", "case_tests": {"T1": ["test_t1_one_mutation"]}}}
        evidence = run_view.evidence(state)
        self.assertEqual([case], evidence["test_cases"])
        self.assertEqual({"T1": ["test_t1_one_mutation"]}, evidence["regression_proof"]["case_tests"])
        state["investigation"]["outcome"] = "not_reproduced"
        self.assertEqual([], run_view.evidence(state)["test_cases"])

    def test_evidence_pairs_criteria_with_their_latest_outcome(self):
        state = {"status": "TASK_COMPLETE", "base_commit": "abc",
                 "goal_contract": {"body": {"intended_outcome": "Fix it", "acceptance_criteria": [
                     {"id": "AC1", "criterion": "Parses dates"}, {"id": "AC2", "criterion": "Documents it"}]}},
                 "last_decision": {"report": {"acceptance_criteria": [
                     {"id": "AC1", "status": "passed", "evidence": "pytest -k dates: 3 passed"}]}},
                 "validation": {"criterion_results": [{"id": "AC1", "status": "FAIL"}]},
                 "human_reviews": {"AC2": {"token": "r1"}},
                 "findings_ledger": [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Typo",
                                      "times_reported": 2}],
                 "regression_proof": {"verdict": "PASS", "fail_to_pass": ["test_dates"], "failures": [],
                                      "unverified": [], "commands": {"suite": "pytest"}, "source_revision": "r9",
                                      "checks": {"large": "output"}}}
        evidence = run_view.evidence(state)
        self.assertEqual(("Fix it", "abc"), (evidence["outcome"], evidence["base_commit"]))
        # validator_status keeps a Validator FAIL distinct from a criterion that was
        # never checked (None), independent of the decision report's own outcome.
        self.assertEqual([{"id": "AC1", "criterion": "Parses dates", "status": "passed",
                           "evidence": "pytest -k dates: 3 passed", "validator_status": "FAIL",
                           "human_reviewed": False},
                          {"id": "AC2", "criterion": "Documents it", "status": None, "evidence": None,
                           "validator_status": None, "human_reviewed": True}], evidence["acceptance"])
        self.assertEqual([{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Typo"}],
                         evidence["findings"])
        self.assertEqual({"verdict": "PASS", "fail_to_pass": ["test_dates"], "failures": [], "unverified": [],
                          "commands": {"suite": "pytest"}, "source_revision": "r9", "case_tests": None},
                         evidence["regression_proof"])

    def test_workflow_is_none_until_recognized(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["workflow"])
        self.assertIsNone(run_view.view({"status": "RUNNING", "workflow": {"kind": None, "then": "x"}})["workflow"])
        self.assertEqual("review", run_view.view({"status": "RUNNING", "workflow": {"kind": "review"}})["workflow"])

    def test_complete_needs_nothing(self):
        view = run_view.view({"status": "TASK_COMPLETE"})
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])

    def test_human_review_comes_before_other_questions(self):
        state = {"status": "WAITING_FOR_USER", "pending_questions": [
            {"id": "d-1", "question": "Accept C2?", "review_criteria": ["C2"], "review_token": "r-9"}]}
        self.assertEqual({"kind": "review", "criteria": ["C2"], "token": "r-9", "question": "Accept C2?"},
                         run_view.needs(state))

    def test_human_review_after_validation_passes_with_no_pending_question(self):
        # autopilot.apply_review_result sets user_request directly, with pending_questions
        # left empty — no question object to read review_criteria/review_token from. Shape
        # matches a real run captured live (2026-09-26): sol PASSED, AC11/AC13 need a person.
        state = {"status": "WAITING_FOR_USER", "pending_questions": [],
                 "displayed_review": "r5:abc@def:ghi",
                 "user_request": {"kind": "human_review", "criteria": ["AC11", "AC13"],
                                  "decision_needed": "Review the current artifact and explicitly approve the listed criteria"}}
        self.assertEqual({"kind": "review", "criteria": ["AC11", "AC13"], "token": "r5:abc@def:ghi",
                          "question": "Review the current artifact and explicitly approve the listed criteria"},
                         run_view.needs(state))

    def test_questions_carry_their_defaults(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "permission"},
                 "pending_questions": [{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                                        "proposed_default": "no", "internal": "x"}]}
        need = run_view.needs(state)
        self.assertEqual(("answer", "permission"), (need["kind"], need["request_kind"]))
        self.assertEqual([{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                           "proposed_default": "no"}], need["questions"])

    def test_a_pending_resolver_request_carries_its_token_and_scope(self):
        public = {"request_id": "r1", "request_token": "t1", "scope": "operational_exhaustion"}
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "blocker"},
                 "pending_questions": [{"id": "q1", "question": "Raise the cap?"}],
                 "resolver_human_request": public,
                 "resolver": {"human_escalations": {"r1": {"status": "pending"}}}}
        need = run_view.needs(state)
        self.assertEqual(("r1", "t1", "operational_exhaustion"),
                         (need["resolver_request_id"], need["resolver_token"], need["resolver_scope"]))
        # A consumed request is not answerable, so it carries none of them.
        state["resolver"]["human_escalations"]["r1"]["status"] = "consumed"
        self.assertFalse({"resolver_request_id", "resolver_token", "resolver_scope"} & set(run_view.needs(state)))

    def test_plan_approval_needs_the_displayed_token(self):
        self.assertEqual({"kind": "approve_plan", "token": "g-1"},
                         run_view.needs({"status": "AWAITING_GOAL_APPROVAL", "displayed_goal": "g-1"}))
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "AWAITING_GOAL_APPROVAL"}))

    def test_pauses(self):
        self.assertEqual({"kind": "planning_budget", "reason": "two calls"},
                         run_view.needs({"status": "PAUSED_PLANNING_BUDGET", "stop_reason": "two calls"}))
        self.assertEqual({"kind": "resume", "reason": "quota"},
                         run_view.needs({"status": "PAUSED_BUDGET", "stop_reason": "quota"}))
        self.assertEqual("resume", run_view.needs({"status": "PLAN_REWORK_REQUIRED"})["kind"])

    def test_rejected_validator_report_exposes_exact_retry_attempt(self):
        state = {"status": "PAUSED_REPEATED_FAILURE", "stop_reason": "report rejected",
                 "settings": {"report_repair": {"max_attempts": 2}},
                 "pending_report_repair": {"error": "Check is not supported by an exact executed Validator event",
                                           "attempts": 2,
                                           "latest_rejected": {"iteration": 1, "output": "/run/sol_report_repair-02.json"}}}
        self.assertEqual("001/sol_report_repair-02", run_view.needs(state)["retry_report_attempt"])

    def test_running_continues(self):
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "RUNNING", "pending_questions": []}))


class TaskRunTests(unittest.TestCase):
    """End to end through the real CLI with the offline fixture provider (a few seconds)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="taskrun-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def test_retired_token_cap_option_is_rejected_before_a_run_starts(self):
        for cap in ("0", "1", "2000000"):
            with self.subTest(cap=cap), self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
                taskrun.TaskRun.start(self.workspace, BRIEF,
                    options=[*FIXTURE_OPTIONS, "--max-reported-tokens", cap], env=self.env)
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_start_approve_and_complete(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            run.approve_plan("not-the-displayed-token")
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertTrue(view["done"], view)
        self.assertTrue((self.workspace / "greet.py").is_file())
        # A new caller can reattach to the saved run.
        again = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS, env=self.env)
        self.assertEqual("TASK_COMPLETE", again.status()["status"])

    def test_usage_errors_are_not_mistaken_for_a_pause(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        broken = taskrun.TaskRun(self.workspace, run.run_dir, options=("--no-such-flag",), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
            broken.advance()

    def test_rejected_verification_change_is_reported_to_the_caller(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        before = (run.run_dir / 'state.json').read_bytes()
        broken = taskrun.TaskRun(self.workspace, run.run_dir,
                                options=('--test-command', 'python -m unittest'), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, 'Changing saved verification commands'):
            broken.resume_paused()
        self.assertEqual(before, (run.run_dir / 'state.json').read_bytes())


class TaskRunClientTests(unittest.TestCase):
    def test_advancing_command_rejects_input_error_instead_of_treating_it_as_a_pause(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        rejected = subprocess.CompletedProcess([], 2,
                                               stdout='Input rejected: pending report repair must be reconciled\n',
                                               stderr='')
        with patch.object(taskrun.subprocess, 'run', return_value=rejected), \
                self.assertRaisesRegex(taskrun.TaskRunError, 'pending report repair'):
            run._invoke('retry failed stage', '--resume-paused', '--retry-failed-stage', advancing=True)

    def test_retry_failed_stage_uses_explicit_inspected_retry(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.retry_failed_stage()
        invoke.assert_called_once_with('retry failed stage', '--resume-paused', '--retry-failed-stage',
                                       '--no-chat', advancing=True)
        status.assert_called_once()

    def test_retry_report_uses_exact_attempt_and_explicit_resume(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.retry_report('001/sol_report_repair-02')
        invoke.assert_called_once_with('retry report', '--resume-paused', '--retry-report',
                                       '001/sol_report_repair-02', '--no-chat', advancing=True)
        status.assert_called_once()

    def test_operational_response_uses_resolver_command_not_question_answer(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_act', return_value={'needs': {'kind': 'resume'}}) as act:
            view = run.respond_operational('request-1', 'token-1', 'Cause identified')
        act.assert_called_once_with('resolver response', '--resolver-request', 'request-1',
                                    '--resolver-token', 'token-1', '--resolver-response',
                                    'provide_information', '--resolver-message', 'Cause identified')
        self.assertEqual('resume', view['needs']['kind'])

    def test_accept_transport_change_requires_explicit_cli_flag(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.accept_transport_change()
        invoke.assert_called_once_with('accept transport change', '--resume-paused',
                                       '--accept-transport-change', '--no-chat', advancing=True)
        status.assert_called_once()

    def test_answer_forwards_the_current_resolver_token(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act", return_value={"needs": {"kind": "answer"}}) as act:
            view = run.answer("Q1", "Use addition.py", resolver_token="current-token")
        act.assert_called_once_with("answer", "--answer", "Q1=Use addition.py",
                                     "--resolver-token", "current-token")
        self.assertEqual("answer", view["needs"]["kind"])

    def test_answer_without_resolver_token_keeps_the_existing_cli_contract(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act") as act:
            run.answer("Q1", "Use addition.py")
        act.assert_called_once_with("answer", "--answer", "Q1=Use addition.py")

    def test_start_preserves_cli_error_when_no_run_was_created(self):
        for stderr, stdout in (("autocode: GoCode authentication check failed", ""),
                               ("", "autocode: startup failed")):
            with self.subTest(stderr=stderr, stdout=stdout), tempfile.TemporaryDirectory() as root:
                completed = subprocess.CompletedProcess([], 2, stdout=stdout, stderr=stderr)
                with patch.object(taskrun.TaskRun, "_invoke", return_value=completed), \
                     self.assertRaises(taskrun.TaskRunError) as raised:
                    taskrun.TaskRun.start(root, "A sample task")
                self.assertIn("start exited 2 without creating a run", str(raised.exception))
                self.assertIn(stderr or stdout, str(raised.exception))

    def test_start_bounds_diagnostic_output_when_no_run_was_created(self):
        with tempfile.TemporaryDirectory() as root:
            completed = subprocess.CompletedProcess([], 2, stdout="", stderr="x" * 2000 + "cause")
            with patch.object(taskrun.TaskRun, "_invoke", return_value=completed), \
                 self.assertRaises(taskrun.TaskRunError) as raised:
                taskrun.TaskRun.start(root, "A sample task")
            self.assertTrue(str(raised.exception).endswith("cause"))
            self.assertLess(len(str(raised.exception)), 1000)

    def test_show_goal_returns_the_current_displayed_brief(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        completed = subprocess.CompletedProcess([], 0, stdout="Build brief r2\nAcceptance criteria:\n  [AC1] Works\n")
        with patch.object(run, "_invoke", return_value=completed) as invoke:
            self.assertEqual(completed.stdout, run.show_goal())
        invoke.assert_called_once_with("show goal", "--show-goal")


if __name__ == "__main__":
    unittest.main()
