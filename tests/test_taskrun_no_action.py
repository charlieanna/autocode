"""A client returns a status observation unless the core offers Continue."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocode_taskrun import TaskRun, TaskRunError


class TaskRunNoActionTests(unittest.TestCase):
    def run_fixture(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return TaskRun(root, root / ".autocode/runs/test")

    @staticmethod
    def observation(*, needs=None, status="RUNNING", iteration=1, done=False):
        return {
            "status": status,
            "iteration": iteration,
            "done": done,
            "needs": needs,
            "next_stage": "tester",
            "phase": "EXECUTING",
            "run_dir": "/run/test",
            "next_command": None,
            "stop_reason": "Preserved core observation",
        }

    def test_no_action_returns_the_exact_observation_without_dispatch_or_completion(self):
        run = self.run_fixture()
        for status in ("RUNNING", "PAUSED_INTERVENTION", "PAUSED_VERIFICATION_UNCERTAIN"):
            with self.subTest(status=status):
                view = self.observation(status=status)
                before = copy.deepcopy(view)
                with patch.object(run, "status", return_value=view), patch.object(run, "_advance") as advance:
                    self.assertIs(view, run.advance_until_input())
                    advance.assert_not_called()
                self.assertEqual(before, view)
                self.assertFalse(view["done"])

    def test_named_continue_can_end_at_a_no_action_observation(self):
        run = self.run_fixture()
        initial = self.observation(needs={"kind": "continue"})
        blocked = self.observation(status="PAUSED_VERIFICATION_UNCERTAIN", iteration=2)
        with (
            patch.object(run, "status", return_value=initial),
            patch.object(run, "_advance", return_value=blocked) as advance,
        ):
            self.assertIs(blocked, run.advance_until_input())
            advance.assert_called_once_with(initial)
        self.assertFalse(blocked["done"])
        self.assertEqual("Preserved core observation", blocked["stop_reason"])

    def test_a_new_stop_is_returned_even_without_counter_changes_or_at_the_call_bound(self):
        run = self.run_fixture()
        initial = self.observation(needs={"kind": "continue"})
        for need, done in ((None, False), ({"kind": "answer"}, False), (None, True)):
            with self.subTest(needs=need, done=done):
                stopped = self.observation(needs=need, done=done)
                with (
                    patch.object(run, "status", return_value=initial),
                    patch.object(run, "_advance", return_value=stopped) as advance,
                ):
                    self.assertIs(stopped, run.advance_until_input(max_calls=1))
                    advance.assert_called_once_with(initial)

    def test_unchanged_continue_still_refuses_another_attempt(self):
        run = self.run_fixture()
        view = self.observation(needs={"kind": "continue"})
        with (
            patch.object(run, "status", return_value=view),
            patch.object(run, "_advance", return_value=view) as advance,
        ):
            with self.assertRaisesRegex(TaskRunError, "no progress"):
                run.advance_until_input()
            advance.assert_called_once_with(view)

    def test_continue_relaunch_bound_is_unchanged(self):
        run = self.run_fixture()
        initial = self.observation(needs={"kind": "continue"})
        later = [self.observation(needs={"kind": "continue"}, iteration=i) for i in (2, 3)]
        with (
            patch.object(run, "status", return_value=initial),
            patch.object(run, "_advance", side_effect=later) as advance,
        ):
            with self.assertRaisesRegex(TaskRunError, "still not stopped after 2 relaunches"):
                run.advance_until_input(max_calls=2)
            self.assertEqual(2, advance.call_count)
