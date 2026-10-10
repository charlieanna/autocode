"""The --json terminal output (#714).

print_completion and print_pause switch the final render between the human summary and
the same JSON payload --status prints. Two invariants matter enough to pin:

- with ``--json``, stdout carries the status payload and no summary prose;
- completion delivery (worktrees.deliver commits the task branch) still runs in both
  modes — it is a side effect, not text.
"""

import contextlib
import copy
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode


class JsonOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.state = {"status": "TASK_COMPLETE", "task": "greet", "iteration": 1}
        self.workspace = Path("/workspace")
        self.run_dir = Path("/run")

    def test_completion_with_json_prints_the_status_payload_and_no_prose(self):
        args = SimpleNamespace(json=True)
        with (
            mock.patch.object(autocode.status_command, "render") as render,
            mock.patch.object(autocode.worktrees, "deliver", return_value="") as deliver,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            autocode.print_completion(self.state, args, self.workspace, self.run_dir)
        render.assert_called_once_with(autocode, self.state, args, self.workspace, self.run_dir)
        deliver.assert_called_once_with(self.state, self.workspace)
        self.assertEqual("", out.getvalue())

    def test_completion_delivery_still_runs_before_the_json_render(self):
        order = []
        args = SimpleNamespace(json=True)
        with (
            mock.patch.object(autocode.status_command, "render", side_effect=lambda *a: order.append("render")),
            mock.patch.object(autocode.worktrees, "deliver", side_effect=lambda *a: order.append("deliver") or ""),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            autocode.print_completion(self.state, args, self.workspace, self.run_dir)
        self.assertEqual(["deliver", "render"], order)

    def test_completion_without_json_prints_the_summary(self):
        args = SimpleNamespace(json=False)
        with (
            mock.patch.object(autocode.status_command, "render") as render,
            mock.patch.object(autocode.jobs, "render", return_value="COMPLETED SUMMARY"),
            mock.patch.object(autocode.worktrees, "deliver", return_value="\nDelivered on branch b (0123456789ab)."),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            autocode.print_completion(self.state, args, self.workspace, self.run_dir)
        render.assert_not_called()
        self.assertIn("COMPLETED SUMMARY", out.getvalue())
        self.assertIn("Delivered on branch b", out.getvalue())

    def test_pause_with_json_prints_the_status_payload(self):
        args = SimpleNamespace(json=True)
        state = {"status": "PAUSED_PROCESS_CLEANUP", "task": "greet", "iteration": 1}
        with (
            mock.patch.object(autocode.status_command, "render") as render,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            autocode.print_pause(state, args, self.workspace, self.run_dir, "prose that must not print")
        render.assert_called_once_with(autocode, state, args, self.workspace, self.run_dir)
        self.assertEqual("", out.getvalue())

    def test_pause_without_json_prints_the_rendered_view(self):
        args = SimpleNamespace(json=False)
        state = {"status": "PAUSED_PROCESS_CLEANUP", "task": "greet", "iteration": 1}
        before = copy.deepcopy(state)
        with (
            mock.patch.object(autocode.status_command, "render") as render,
            mock.patch.object(autocode.lifecycle, "present") as present,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            autocode.print_pause(state, args, self.workspace, self.run_dir, "PAUSED_PROCESS_CLEANUP: stopped")
        render.assert_not_called()
        present.assert_not_called()
        self.assertEqual(before, state)
        self.assertIn("AutoCode\nState: PAUSED_PROCESS_CLEANUP\nNext command:", out.getvalue())
        self.assertTrue(out.getvalue().endswith("PAUSED_PROCESS_CLEANUP: stopped\n"))

    def test_pause_prose_is_rendered_by_the_caller_before_the_state_write(self):
        """present() mutates state (drops a stale displayed_goal), so the caller must
        render before persisting; the helper must not re-render or defer it."""
        args = SimpleNamespace(json=True)
        state = {"status": "WAITING_FOR_USER", "displayed_goal": {"id": "g1"}}
        with mock.patch.object(autocode.status_command, "render"):
            with contextlib.redirect_stdout(io.StringIO()):
                autocode.print_pause(state, args, self.workspace, self.run_dir, "already rendered")
        self.assertIn("displayed_goal", state)  # the helper itself never touches state


class JsonFlagTests(unittest.TestCase):
    def test_the_main_parser_accepts_the_flag(self):
        import autocode_args
        import autocode_configure

        args, _ = autocode_args.parse(None, ["greet", "--json"], autocode_configure.DEFAULT_ROLE_MODELS)
        self.assertTrue(args.json)
        self.assertEqual("greet", args.task)

    def test_the_flag_defaults_off(self):
        import autocode_args
        import autocode_configure

        args, _ = autocode_args.parse(None, ["greet"], autocode_configure.DEFAULT_ROLE_MODELS)
        self.assertFalse(args.json)
