"""Live activity in the chat view: what the running attempt does, from its own event file, display only."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_chat_activity as activity

from tests.test_liveness import inspection, metadata


def codex(kind, identifier, **item):
    return {"type": kind, "item": {"id": identifier, **item}}


def write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class ReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.events = Path(temp.name) / "builder-01.jsonl"

    def test_codex_items_become_commands_edits_and_tools(self):
        write(
            self.events,
            [
                {"type": "thread.started", "thread_id": "t"},
                codex("item.started", "i1", type="command_execution", command="pytest -q\nsecond line"),
                codex("item.completed", "i1", type="command_execution", command="pytest -q\nsecond line", exit_code=0),
                codex(
                    "item.completed",
                    "i2",
                    type="file_change",
                    changes=[
                        {"path": "/work/app/greet.py", "kind": "add"},
                        {"path": "/work/app/README.md", "kind": "update"},
                    ],
                ),
                codex("item.started", "i3", type="mcp_tool_call", name="docs.search", status="in_progress"),
                codex("item.completed", "i4", type="agent_message", text="I will now edit greet.py"),
                codex("item.completed", "i5", type="reasoning", text="private thoughts"),
            ],
        )
        self.assertEqual(
            [
                {"kind": "command", "text": "pytest -q", "status": "done"},
                {
                    "kind": "edit",
                    "text": "added app/greet.py, edited app/README.md",
                    "paths": ["app/greet.py", "app/README.md"],
                    "status": "done",
                },
                {"kind": "tool", "text": "docs.search", "status": "running"},
            ],
            activity.read(self.events, "/work"),
            "prose and reasoning are never copied; a repeated item is one event with its latest state",
        )

    def test_a_failing_command_is_failed_not_done(self):
        write(self.events, [codex("item.completed", "i1", type="command_execution", command="false", exit_code=1)])
        self.assertEqual("failed", activity.read(self.events)[0]["status"])

    def test_opencode_and_kilo_tool_parts(self):
        def part(identifier, tool, status, **given):
            return {
                "type": "tool_use",
                "sessionID": "s",
                "part": {"id": identifier, "type": "tool", "tool": tool, "state": {"status": status, "input": given}},
            }

        write(
            self.events,
            [
                part("p1", "bash", "completed", command="npm test"),
                part("p2", "edit", "completed", filePath="/work/src/a.ts"),
                part("p3", "write", "running", filePath="/work/src/b.ts"),
                part("p4", "read", "completed", filePath="/work/src/a.ts", offset=0),
                part("p5", "grep", "error", pattern="TODO"),
                {"type": "text", "sessionID": "s", "part": {"id": "t1", "type": "text", "text": "prose"}},
            ],
        )
        self.assertEqual(
            [
                ("command", "npm test", "done"),
                ("edit", "edited src/a.ts", "done"),
                ("edit", "added src/b.ts", "running"),
                ("tool", "read src/a.ts", "done"),
                ("tool", "grep TODO", "failed"),
            ],
            [(row["kind"], row["text"], row["status"]) for row in activity.read(self.events, "/work")],
        )

    def test_only_the_newest_events_are_kept_and_unreadable_rows_are_skipped(self):
        rows = [
            codex("item.completed", f"i{n}", type="command_execution", command=f"cmd {n}", exit_code=0)
            for n in range(30)
        ]
        self.events.write_text("not json\n[1, 2]\n" + "".join(json.dumps(row) + "\n" for row in rows))
        shown = activity.read(self.events)
        self.assertEqual(activity.LIMIT, len(shown))
        self.assertEqual("cmd 29", shown[-1]["text"])
        self.assertEqual("cmd 10", shown[0]["text"])

    def test_a_long_command_is_cut_and_control_characters_never_reach_a_terminal(self):
        write(
            self.events,
            [codex("item.completed", "i1", type="command_execution", command="echo \x1b[31m" + "x" * 500, exit_code=0)],
        )
        [event] = activity.read(self.events)
        self.assertLessEqual(len(event["text"]), activity.TEXT_LIMIT)
        self.assertNotIn("\x1b", event["text"])
        self.assertTrue(event["text"].endswith("…"))

    def test_a_missing_file_is_no_activity(self):
        self.assertEqual([], activity.read(self.events))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name).resolve()
        self.events = self.run / "iterations" / "builder-01.jsonl"
        self.events.parent.mkdir()
        write(self.events, [codex("item.completed", "i1", type="command_execution", command="ls", exit_code=0)])
        self.state = {"active_stage": {"stage": "terra", "events": str(self.events)}, "workspace": "/work"}
        self.public = {"status": "RUNNING", "needs": {"kind": "continue"}, "liveness": {"kind": "supervised"}}

    def project(self, **flags):
        return activity.project(self.state, self.public, self.run, **flags)

    def test_a_supervised_working_attempt_shows_its_events(self):
        self.assertEqual([{"kind": "command", "text": "ls", "status": "done"}], self.project())

    def test_activity_that_cannot_be_confirmed_or_is_over_is_not_shown(self):
        for kind in ("unknown", "unsupervised", "interrupted", "stopped"):
            self.public["liveness"] = {"kind": kind}
            self.assertEqual([], self.project(), kind)
        self.public["liveness"] = {"kind": "supervised"}
        self.assertEqual([], self.project(finished=True))
        self.assertEqual([], self.project(stopped=True))
        self.public["status"] = "PAUSED_REQUESTED"
        self.assertEqual([], self.project())

    def test_a_file_outside_the_run_directory_is_never_read(self):
        outside = self.run.parent / "elsewhere.jsonl"
        write(outside, [codex("item.completed", "i1", type="command_execution", command="secret", exit_code=0)])
        self.state["active_stage"]["events"] = str(outside)
        self.assertEqual([], self.project())
        link = self.run / "iterations" / "link.jsonl"
        link.symlink_to(outside)
        self.state["active_stage"]["events"] = str(link)
        self.assertEqual([], self.project(), "a link out of the run is no better")
        outside.unlink()

    def test_the_inputs_are_not_changed(self):
        before = json.dumps([self.state, self.public], sort_keys=True)
        self.project()
        self.assertEqual(before, json.dumps([self.state, self.public], sort_keys=True))


class StatusViewTests(unittest.TestCase):
    """`--status` carries ``chat.activity`` while a supervised attempt works, and omits it otherwise."""

    def render(self, kind, events_rows):
        import autocode as runner
        import autocode_status_command as command

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve()
            events = run / "builder-01.jsonl"
            write(events, events_rows)
            saved = metadata()
            state = {
                "status": "RUNNING",
                "iteration": 1,
                "sessions": {},
                "next_stage": "terra",
                "stages": [],
                "active_stage": {
                    "stage": "terra",
                    "iteration": 1,
                    "output": str(run / "builder-01.json"),
                    "events": str(events),
                    "supervision": saved,
                },
            }
            args = SimpleNamespace(inspect_evidence=False, run_dir="fixture", engine=None)
            output = io.StringIO()
            with (
                patch.object(
                    runner.processes,
                    "recorded_worker_state",
                    return_value={"checked": True, "alive": True, "live_pids": [101]},
                ),
                patch.object(runner.supervision, "observe", return_value=inspection(saved, **kind)),
                patch.object(runner, "stage_completed", return_value=False),
                patch.object(runner.milestones, "summary", return_value={"accepted_milestones": []}),
                patch.object(runner.dependency, "export", return_value={}),
                patch.object(runner, "intervention_metadata", return_value={}),
                patch.object(runner.resolver_human, "projection", return_value={}),
                redirect_stdout(output),
                redirect_stderr(io.StringIO()),
            ):
                command.render(runner, state, args, Path(tmp), run)
            return json.loads(output.getvalue())["view"]["chat"]

    def test_a_supervised_attempt_shows_its_activity_beside_its_progress_lines(self):
        chat = self.render({}, [codex("item.started", "i1", type="command_execution", command="pytest -q")])
        self.assertEqual([{"kind": "command", "text": "pytest -q", "status": "running"}], chat["activity"])
        self.assertTrue(chat["progress"], "the existing progress lines are unchanged")

    def test_an_attempt_without_supervision_shows_none(self):
        chat = self.render({"keeper": False}, [codex("item.started", "i1", type="command_execution", command="ls")])
        self.assertNotIn("activity", chat)


if __name__ == "__main__":
    unittest.main()
