"""`autocode events`: the run's own records, verbatim; the status view ends the stream."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_events_command as events
import autocode_subcommands as subcommands
import autocode_util as util

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "schemas" / "run-event.schema.json"

RECORDED = [
    {"kind": "build_start", "actor": "user_cli", "at": "2026-10-14T05:00:00+00:00", "token": "tok"},
    {"kind": "goal_approval", "actor": "user_cli", "at": "2026-10-14T05:01:00+00:00", "token": "abc"},
]


def saved_run(root: Path, name: str = "saved", **written) -> Path:
    """A run directory whose checkpoint carries the recorded events, plus overrides."""
    run_dir = root / ".autocode" / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 3,
        "workspace": str(root),
        "task": "build the thing",
        "status": "RUNNING",
        "user_events": [dict(event) for event in RECORDED],
        **written,
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    return run_dir


def project(**written) -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / ".git").mkdir()
    saved_run(root, **written)
    return root


class EventsCommandTests(unittest.TestCase):
    def capture(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = events.cli(argv)
        return code, out.getvalue(), err.getvalue()

    def lines(self, printed: str) -> list[dict]:
        return [json.loads(line) for line in printed.splitlines() if line.strip()]

    def test_it_prints_the_runs_own_records_verbatim_as_json_lines(self):
        root = project()
        code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(0, code)
        self.assertEqual("", err)
        self.assertEqual(RECORDED, self.lines(out))
        self.assertEqual([json.dumps(event) for event in RECORDED], out.splitlines())

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

    def test_a_run_dir_alone_streams_a_run_saved_beside_its_project(self):
        # A scenario keeps its run directory beside the project it ran in rather
        # than under that project's .autocode/runs; --run-dir alone still acts on it.
        root = Path(tempfile.mkdtemp()).resolve()
        project_dir = root / "project"
        project_dir.mkdir()
        run_dir = root / "scenario-run"
        run_dir.mkdir()
        (run_dir / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "workspace": str(project_dir),
                    "task": "build the thing",
                    "status": "TASK_COMPLETE",
                    "user_events": [dict(event) for event in RECORDED],
                }
            )
        )
        with mock.patch.object(os, "getcwd", return_value="/"):
            code, out, err = self.capture(["--run-dir", str(run_dir)])
        self.assertEqual(0, code, err)
        self.assertEqual(RECORDED, self.lines(out))

    def test_a_run_dir_inside_its_own_project_needs_no_workspace(self):
        root = project()
        with mock.patch.object(os, "getcwd", return_value="/"):
            code, out, err = self.capture(["--run-dir", str(root / ".autocode" / "runs" / "saved")])
        self.assertEqual(0, code, err)
        self.assertEqual(RECORDED, self.lines(out))

    def test_an_unreadable_checkpoint_says_why_it_was_not_streamed(self):
        root = Path(tempfile.mkdtemp()).resolve()
        run_dir = root / ".autocode" / "runs" / "broken"
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text("{not json")
        code, out, err = self.capture(["--workspace", str(root)])
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("autocode events:", err)

    def test_follow_on_a_completed_run_prints_what_it_recorded_and_ends_at_zero(self):
        root = project(status="TASK_COMPLETE")
        code, out, err = self.capture(["--workspace", str(root), "--follow"])
        self.assertEqual(0, code)
        self.assertEqual(RECORDED, self.lines(out))
        self.assertIn("run TASK_COMPLETE after 2 event(s)", err)

    def test_follow_ends_where_the_contract_says_a_person_is_needed(self):
        root = project(status="PAUSED_REQUESTED", stop_reason="paused on request")
        with mock.patch.object(util, "sleep") as wait:
            code, out, err = self.capture(["--workspace", str(root), "--follow"])
        self.assertEqual(2, code)
        self.assertEqual(RECORDED, self.lines(out))
        self.assertIn("needs you", err)
        self.assertIn("--resume-paused", err)
        wait.assert_not_called()

    def test_follow_prints_the_events_a_working_run_appends(self):
        root = project(user_events=[dict(RECORDED[0])])
        run_dir = root / ".autocode" / "runs" / "saved"
        state_path = run_dir / "state.json"
        appended = [
            {"kind": "route_assignment", "actor": "runner", "at": "2026-10-14T05:02:00+00:00", "role": "terra"},
            {"kind": "completion_accept", "actor": "user_cli", "at": "2026-10-14T05:03:00+00:00", "basis": "evidence"},
        ]

        def advance(_seconds):
            state = json.loads(state_path.read_text())
            state["user_events"].append(appended[len(state["user_events"]) - 1])
            if len(state["user_events"]) == 3:
                state["status"] = "TASK_COMPLETE"
            state_path.write_text(json.dumps(state))

        with mock.patch.object(util, "sleep", side_effect=advance) as wait:
            code, out, err = self.capture(["--workspace", str(root), "--follow"])
        self.assertEqual(0, code, err)
        self.assertEqual([RECORDED[0], *appended], self.lines(out))
        self.assertEqual([mock.call(events.POLL_SECONDS)] * 2, wait.call_args_list)
        self.assertIn("run TASK_COMPLETE after 3 event(s)", err)

    def test_a_bounded_follow_stops_with_the_run_still_working(self):
        root = project()
        clock = itertools.count(0, 2)
        with (
            mock.patch.object(util, "monotonic", side_effect=lambda: float(next(clock))),
            mock.patch.object(util, "sleep"),
        ):
            code, out, err = self.capture(["--workspace", str(root), "--follow", "--timeout", "3"])
        self.assertEqual(2, code)
        self.assertEqual(RECORDED, self.lines(out))
        self.assertIn("--timeout 3s reached with the run still working", err)

    def test_a_bound_without_a_follow_is_a_usage_error(self):
        root = project()
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as raised,
        ):
            events.cli(["--workspace", str(root), "--timeout", "5"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--timeout needs --follow", err.getvalue())
        self.assertEqual("", out.getvalue())

    def test_the_subcommand_table_dispatches_it(self):
        root = project()
        self.assertIn("events", subcommands.command_names())
        self.assertEqual("autocode_events_command", subcommands.SUBCOMMANDS["events"])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = subcommands.dispatch(["events", "--workspace", str(root)])
        self.assertEqual(0, code)
        self.assertEqual(RECORDED, self.lines(out.getvalue()))


class RunEventSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text())

    def test_the_shipped_schema_documents_an_open_envelope(self):
        self.assertEqual("autocode:schemas/run-event@1", self.schema["$id"])
        self.assertEqual("object", self.schema["type"])
        self.assertEqual(["kind"], self.schema["required"])
        self.assertIs(True, self.schema["additionalProperties"])
        for field in ("kind", "at", "actor"):
            self.assertEqual("string", self.schema["properties"][field]["type"], field)

    def test_every_streamed_event_carries_the_envelope_it_documents(self):
        root = project()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, events.cli(["--workspace", str(root)]))
        for line in out.getvalue().splitlines():
            event = json.loads(line)
            self.assertIsInstance(event["kind"], str)
            self.assertTrue(event["kind"])
            self.assertIsInstance(event.get("at", ""), str)
            self.assertIsInstance(event.get("actor", ""), str)


if __name__ == "__main__":
    unittest.main()
