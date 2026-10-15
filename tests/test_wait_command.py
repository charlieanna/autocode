"""`autocode wait`: block until the run needs you, ends, or loses its controller."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_controller_owner as controller_owner
import autocode_subcommands as subcommands
import autocode_util as util
import autocode_wait_command as wait_command

RUNNING_CONTROLLER = {"kind": "running", "reason": "Birth-bound controller is alive", "checked": True, "alive": True}


def saved_run(root: Path, name: str = "saved", **written) -> Path:
    run_dir = root / ".autocode" / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {"version": 3, "workspace": str(root), "task": "build the thing", "status": "RUNNING", **written}
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def project(**written) -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / ".git").mkdir()
    saved_run(root, **written)
    return root


class WaitCommandTests(unittest.TestCase):
    def capture(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wait_command.cli(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_completed_run_ends_the_wait_at_zero_without_polling(self):
        root = project(status="TASK_COMPLETE")
        with mock.patch.object(util, "sleep") as sleep:
            code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(0, code, err)
        self.assertEqual("", err)
        self.assertIn("complete", out)
        self.assertIn("TASK_COMPLETE", out)
        sleep.assert_not_called()

    def test_a_run_that_needs_a_person_ends_the_wait_naming_its_command(self):
        root = project(status="PAUSED_REQUESTED", stop_reason="paused on request")
        with mock.patch.object(util, "sleep") as sleep:
            code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertIn("needs_you", out)
        self.assertIn("--resume-paused", out)
        sleep.assert_not_called()

    def test_a_dependency_wait_names_the_wait_on_its_producer_run(self):
        root = project(
            status="WAITING_FOR_DEPENDENCY",
            stop_reason="Waiting for prerequisite task to finish and pass independent review.",
            dependency_wait={
                "producer_run": "/tmp/producer-ws/.autocode/runs/producer",
                "producer_workspace": "/tmp/producer-ws",
            },
        )
        with mock.patch.object(util, "sleep") as sleep:
            code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code, err)
        self.assertIn("needs_you", out)
        self.assertIn("autocode wait --run-dir /tmp/producer-ws/.autocode/runs/producer", out)
        sleep.assert_not_called()

    def test_an_unowned_running_checkpoint_ends_the_wait_as_controller_lost(self):
        # No controller receipt, both exclusion locks free, no workers recorded: the
        # saved RUNNING status is a claim nothing alive backs, which is the gap the
        # saved-checkpoint table cannot see.
        root = project()
        with mock.patch.object(util, "sleep") as sleep:
            code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertIn("controller_lost", out)
        self.assertIn("No controller or provider owns the saved running checkpoint", out)
        self.assertIn(f"autocode --run-dir {root / '.autocode' / 'runs' / 'saved'}", out)
        self.assertIn("--status", out)
        sleep.assert_not_called()

    def test_a_run_with_a_live_controller_waits_until_it_needs_a_person(self):
        root = project()
        state_path = root / ".autocode" / "runs" / "saved" / "state.json"

        def advance(_seconds):
            state = json.loads(state_path.read_text())
            state.update(status="PAUSED_REQUESTED", stop_reason="paused on request")
            state_path.write_text(json.dumps(state))

        with (
            mock.patch.object(controller_owner, "inspect", return_value=dict(RUNNING_CONTROLLER)) as inspect,
            mock.patch.object(util, "sleep", side_effect=advance) as sleep,
        ):
            code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code, err)
        self.assertIn("needs_you", out)
        self.assertEqual(1, sleep.call_count)
        self.assertEqual(1, inspect.call_count)

    def test_a_bounded_wait_reports_the_last_observed_state(self):
        root = project()
        clock = itertools.count(0, 2)
        with (
            mock.patch.object(util, "monotonic", side_effect=lambda: float(next(clock))),
            mock.patch.object(controller_owner, "inspect", return_value=dict(RUNNING_CONTROLLER)),
            mock.patch.object(util, "sleep"),
        ):
            code, out, err = self.capture(["--workspace", str(root), "--timeout", "3", "--json"])
        self.assertEqual(2, code)
        payload = json.loads(out)
        self.assertEqual("timeout", payload["outcome"])
        self.assertEqual("RUNNING", payload["status"])
        self.assertEqual("running", payload["controller"])
        self.assertIn("--timeout 3s reached; run still RUNNING", payload["reason"])

    def test_json_carries_one_object_with_the_outcome(self):
        root = project(status="TASK_COMPLETE")
        code, out, err = self.capture(["--workspace", str(root), "--json"])
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual(1, payload["schema"])
        self.assertEqual("complete", payload["outcome"])
        self.assertEqual(str(root), payload["workspace"])
        self.assertIn("waited_seconds", payload)
        self.assertEqual("", err)

    def test_a_workspace_with_no_run_is_named_rather_than_guessed(self):
        root = Path(tempfile.mkdtemp()).resolve()
        code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn(f"no saved run in {root}", err)

    def test_several_saved_runs_are_never_picked_between(self):
        root = Path(tempfile.mkdtemp()).resolve()
        first = saved_run(root, "one")
        second = saved_run(root, "two")
        code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("--run-dir", err)
        self.assertIn(str(first), err)
        self.assertIn(str(second), err)

    def test_an_unreadable_checkpoint_is_reported_not_guessed_from(self):
        root = Path(tempfile.mkdtemp()).resolve()
        run_dir = root / ".autocode" / "runs" / "broken"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{not json")
        code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertIn("unreadable", out)

    def test_a_nonpositive_timeout_is_a_usage_error(self):
        root = project()
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as raised,
        ):
            wait_command.cli(["--workspace", str(root), "--timeout", "0"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--timeout must be greater than zero", err.getvalue())
        self.assertEqual("", out.getvalue())

    def test_a_status_view_that_cannot_read_the_checkpoint_is_a_stop_not_a_crash(self):
        root = project()
        run_dir = root / ".autocode" / "runs" / "saved"
        with mock.patch("autocode_run_view.action", side_effect=ValueError("schema drift")):
            result = wait_command.outcome({"status": "RUNNING"}, run_dir, root)
        self.assertEqual("stopped", result["outcome"])
        self.assertIn("schema drift", result["reason"])

    def test_the_subcommand_table_dispatches_it(self):
        root = project(status="TASK_COMPLETE")
        self.assertIn("wait", subcommands.command_names())
        self.assertEqual("autocode_wait_command", subcommands.SUBCOMMANDS["wait"])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = subcommands.dispatch(["wait", "--workspace", str(root)])
        self.assertEqual(0, code)
        self.assertIn("complete", out.getvalue())


if __name__ == "__main__":
    unittest.main()
