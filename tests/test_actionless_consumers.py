"""Public clients keep an actionless incomplete observation without inventing a decision."""

import contextlib
import copy
import io
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_demo as demo
import autocode_issue as issue
import autocode_multicomponent as components
from autocode_taskrun import TaskRunError

from scenarios.harness.driver import DriveError, Driver


class ActionlessConsumerTests(unittest.TestCase):
    def setUp(self):
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("No process")))
        self.network = self.enterContext(
            patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        )
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def tearDown(self):
        self.spawn.assert_not_called()
        self.network.assert_not_called()

    @staticmethod
    def public(need=None, *, done=False):
        return {
            "status": "RUNNING",
            "done": done,
            "needs": need,
            "next_command": None,
            "next_stage": "builder",
            "iteration": 1,
            "phase": "EXECUTING",
            "stop_reason": "The exact failed attempt remains unresolved.",
            "usage": {"accounting": {"attempts": [{"identity": "original", "exit_code": None}]}},
            "evidence": {"check_replay": {"verdict": "PASS"}},
        }

    @staticmethod
    def observed(call):
        try:
            return call(), None
        except Exception as error:
            return None, error

    def test_component_driver_returns_initial_or_later_actionless_view_for_either_approval_mode(self):
        client = components.MultiComponentBuild.__new__(components.MultiComponentBuild)
        client.max_advances = 4
        for automatic in (False, True):
            for continued in (False, True):
                with self.subTest(auto_approve=automatic, continued=continued):
                    stopped = self.public()
                    before = copy.deepcopy(stopped)
                    run = Mock()
                    run.status.return_value = self.public({"kind": "continue"}) if continued else stopped
                    run.advance_until_input.return_value = stopped
                    result, error = self.observed(lambda: client._drive(run, automatic))
                    self.assertIsNone(error, repr(error))
                    self.assertIs(stopped, result)
                    self.assertEqual(before, stopped)
                    if continued:
                        run.advance_until_input.assert_called_once_with(4)
                    else:
                        run.advance_until_input.assert_not_called()
                    run.approve_plan.assert_not_called()
                    run.answer.assert_not_called()
                    run.approve_review.assert_not_called()

    def test_component_genuine_plan_answer_review_and_resume_keep_their_existing_authority(self):
        client = components.MultiComponentBuild.__new__(components.MultiComponentBuild)
        client.max_advances = 4
        plan = self.public({"kind": "approve_plan", "token": "fresh-plan"})
        answer = self.public({"kind": "answer", "questions": [{"id": "Q1", "options": ["local"]}]})
        review = self.public({"kind": "review", "token": "fresh-review", "criteria": ["C1"]})
        complete = self.public(done=True)
        run = Mock()
        run.status.side_effect = [plan, review, complete]
        run.approve_plan.return_value = answer
        self.assertIs(complete, client._drive(run, True))
        run.approve_plan.assert_called_once_with("fresh-plan")
        run.answer.assert_called_once_with("Q1", "local")
        run.approve_review.assert_called_once_with("C1", "fresh-review")
        resume = self.public({"kind": "resume"})
        run = Mock()
        run.status.return_value = resume
        self.assertIs(resume, client._drive(run, True))
        run.advance_until_input.assert_not_called()

    def issue_fixture(self):
        record = {
            "owner": "example",
            "repo": "project",
            "number": 7,
            "title": "Requested change",
            "project": str(self.root),
            "worktree": str(self.root),
            "run_dir": str(self.root / "run"),
            "branch": "task",
        }
        run = Mock()
        self.enterContext(patch.object(issue, "project_root", return_value=self.root))
        self.enterContext(patch.object(issue, "_ref", return_value=None))
        self.enterContext(patch.object(issue, "load", return_value=record))
        self.enterContext(patch.object(issue, "task_run", return_value=run))
        return record, run, SimpleNamespace(project=str(self.root), chat=False, base=None)

    def test_issue_status_and_continue_render_actionless_failure_without_a_machine_action(self):
        _, run, args = self.issue_fixture()
        for command in (issue.cmd_status, issue.cmd_continue):
            for continued in (False, True) if command is issue.cmd_continue else (False,):
                with self.subTest(command=command.__name__, continued=continued):
                    stopped = self.public()
                    before = copy.deepcopy(stopped)
                    run.reset_mock()
                    run.status.return_value = self.public({"kind": "continue"}) if continued else stopped
                    run.advance_until_input.return_value = stopped
                    shown = io.StringIO()
                    with contextlib.redirect_stdout(shown):
                        result, error = self.observed(lambda: command(args))
                    self.assertIsNone(error, repr(error))
                    self.assertEqual(0, result)
                    self.assertIn(stopped["stop_reason"], shown.getvalue())
                    self.assertNotIn("--resume", shown.getvalue())
                    self.assertNotIn("Talk it through", shown.getvalue())
                    self.assertEqual(before, stopped)
                    self.assertEqual(int(continued), run.advance_until_input.call_count)

    def test_issue_plan_refuses_actionless_result_before_any_plan_publication(self):
        _, run, args = self.issue_fixture()
        run.status.return_value = self.public()
        with patch.object(issue.issue_plan, "prepare") as prepare, patch.object(issue, "save") as save:
            _, error = self.observed(lambda: issue.cmd_plan(args))
        self.assertIsInstance(error, issue.IssueError)
        self.assertIn("not waiting for its plan", str(error))
        prepare.assert_not_called()
        save.assert_not_called()
        run.approve_plan.assert_not_called()

    def test_issue_genuine_plan_offer_keeps_its_exact_token(self):
        record, _, _ = self.issue_fixture()
        lines = issue.next_steps(record, self.public({"kind": "approve_plan", "token": "fresh-plan"}))
        self.assertIn("--approve-goal fresh-plan", "\n".join(lines))

    def test_issue_no_action_fallback_does_not_accept_a_missing_or_malformed_need(self):
        record, _, _ = self.issue_fixture()
        stopped = self.public()
        stopped.pop("stop_reason")
        result, error = self.observed(lambda: issue.next_steps(record, stopped))
        self.assertIsNone(error, repr(error))
        self.assertEqual(["AutoCode names no action for this run."], result)
        for malformed in ("missing", False, [], "continue"):
            with self.subTest(malformed=malformed):
                bad = self.public()
                if malformed == "missing":
                    bad.pop("needs")
                else:
                    bad["needs"] = malformed
                with self.assertRaisesRegex(ValueError, "no needs record"):
                    issue.next_steps(record, bad)

    def test_demo_genuine_answer_and_plan_approval_keep_the_exact_token_and_displayed_plan(self):
        run = Mock()
        run.workspace = self.root / "greeting"
        environment = self.root / "greeting-environment"
        environment.mkdir()
        question = self.public({"kind": "answer", "questions": [{"id": "Q1"}], "resolver_token": "question-current"})
        plan = self.public({"kind": "approve_plan", "token": "plan-current"})
        stopped = self.public()
        run.advance_until_input.side_effect = [question, plan, stopped]
        run.show_goal.return_value = "The exact displayed plan"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(stopped, demo._plan(run))
        run.answer.assert_called_once_with("Q1", "CLI", resolver_token="question-current")
        run.approve_plan.assert_called_once_with("plan-current")
        self.assertEqual("The exact displayed plan", (environment / "displayed-plan.txt").read_text())

    def test_demo_reports_its_domain_failure_initially_and_after_a_genuine_answer(self):
        for answered in (False, True):
            with self.subTest(answered=answered):
                stopped = self.public()
                run = Mock()
                question = self.public({"kind": "answer", "questions": [{"id": "Q1"}]})
                run.advance_until_input.side_effect = [question, stopped] if answered else [stopped]
                with contextlib.redirect_stdout(io.StringIO()):
                    _, error = self.observed(lambda: demo._plan(run))
                self.assertIsInstance(error, TaskRunError)
                self.assertIn("approval-ready plan", str(error))
                self.assertIn(stopped["stop_reason"], str(error))
                self.assertEqual(int(answered), run.answer.call_count)
                run.approve_plan.assert_not_called()

    def test_demo_review_boundary_fails_honestly_without_accepting_or_completing(self):
        refused = self.public()
        refused["stop_reason"] = "was reported as exit 0 but exited 1"
        stopped = self.public()
        run = Mock()
        with (
            patch.object(demo, "_prepare", return_value=run),
            patch.object(demo, "_plan", side_effect=[refused, stopped]),
            patch.object(demo, "_save"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            _, error = self.observed(lambda: demo.run_demo(self.root))
        self.assertIsInstance(error, TaskRunError)
        self.assertIn("human acceptance", str(error))
        self.assertFalse(stopped["done"])
        run.approve_review.assert_not_called()

    def driver(self):
        client = Driver(self.root / "project", self.root, [], {}, autocode=["autocode"], max_steps=3, timeout_seconds=5)
        client.run_dir = self.root / "project/.autocode/runs/test"
        self.enterContext(patch.object(client, "assert_needs_command_accepted"))
        self.enterContext(patch.object(client, "pause_to_acknowledge", return_value=None))
        self.enterContext(patch.object(client, "answered_explicitly", return_value=False))
        return client

    def test_driver_actionless_crash_view_never_resumes(self):
        client = self.driver()
        with patch.object(client, "view", return_value=self.public()), patch.object(client, "call") as call:
            result, error = self.observed(client.resume_after_crash)
        self.assertIsNone(error, repr(error))
        self.assertIsNone(result)
        call.assert_not_called()

    def test_driver_initial_actionless_returns_exactly_and_completed_controller_resume_remains_named(self):
        client = self.driver()
        stopped = self.public()
        with patch.object(client, "view", return_value=stopped), patch.object(client, "call") as call:
            self.assertIs(stopped, client.until_stopped())
            call.assert_not_called()
        resume = self.public({"kind": "resume"})
        resume["controller"] = {"completed_stage": True}
        with patch.object(client, "view", return_value=resume), patch.object(client, "call") as call:
            client.resume_after_crash()
            call.assert_called_once_with("resume", "--resume-paused")

    def test_driver_returns_new_no_action_input_or_done_before_unchanged_progress_check(self):
        for need, done in ((None, False), ({"kind": "approve_plan", "token": "fresh"}, False), (None, True)):
            with self.subTest(need=need, done=done):
                client = self.driver()
                initial = self.public({"kind": "continue"})
                stopped = self.public(need, done=done)
                before = copy.deepcopy(stopped)
                with patch.object(client, "view", side_effect=[initial, stopped]), patch.object(client, "call") as call:
                    result, error = self.observed(lambda: client.until_stopped(say_at="needs:approve_plan"))
                self.assertIsNone(error, repr(error))
                self.assertIs(stopped, result)
                self.assertEqual(before, stopped)
                call.assert_called_once_with("resume")

    def test_driver_unchanged_continue_still_refuses_without_another_call(self):
        client = self.driver()
        initial = self.public({"kind": "continue"})
        with patch.object(client, "view", return_value=initial), patch.object(client, "call") as call:
            _, error = self.observed(client.until_stopped)
        self.assertIsInstance(error, DriveError)
        self.assertIn("no progress", str(error))
        call.assert_called_once_with("resume")
