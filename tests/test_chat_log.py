"""The chat's live agent log: each running agent's reasoning, messages and tool calls, display only."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_chat_log as log

from tests.test_liveness import inspection, metadata


def codex(kind, identifier, **item):
    return {"type": kind, "item": {"id": identifier, **item}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class ReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.events = Path(temp.name) / "builder-01.jsonl"

    def entries(self):
        return [(entry["kind"], entry["text"]) for _, entry in log.read(self.events, "/work")]

    def test_reasoning_messages_and_tool_calls_in_the_order_they_began(self):
        write(
            self.events,
            [
                codex("item.updated", "r1", type="reasoning", text="I should look at"),
                codex("item.started", "c1", type="command_execution", command="pytest -q"),
                codex("item.updated", "r1", type="reasoning", text="I should look at greet.py first"),
                codex("item.completed", "c1", type="command_execution", command="pytest -q", exit_code=0),
                codex("item.completed", "m1", type="agent_message", text="Editing greet.py now."),
                codex(
                    "item.completed", "f1", type="file_change", changes=[{"path": "/work/greet.py", "kind": "update"}]
                ),
            ],
        )
        self.assertEqual(
            [
                ("thinking", "I should look at greet.py first"),
                ("command", "pytest -q"),
                ("message", "Editing greet.py now."),
                ("edit", "edited greet.py"),
            ],
            self.entries(),
            "a growing thought is one entry with its newest text, in the place it started",
        )

    def test_opencode_and_kilo_text_and_reasoning_parts(self):
        write(
            self.events,
            [
                {"type": "reasoning", "part": {"id": "p1", "type": "reasoning", "text": "Plan: read then edit"}},
                {"type": "text", "part": {"id": "p2", "type": "text", "text": "Done reading."}},
                {
                    "type": "tool_use",
                    "part": {"id": "p3", "tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}}},
                },
            ],
        )
        self.assertEqual(
            [("thinking", "Plan: read then edit"), ("message", "Done reading."), ("command", "ls")], self.entries()
        )

    def test_prose_is_one_line_without_control_characters_or_secrets_and_keeps_its_newest_words(self):
        write(
            self.events,
            [
                codex("item.completed", "r1", type="reasoning", text="first\n\n\x1b[31mred\x07 api_key=abcdefghijkl"),
                codex("item.completed", "r2", type="reasoning", text="old " * 200 + "newest"),
            ],
        )
        (first, second) = [text for _, text in self.entries()]
        self.assertNotIn("\x1b", first)
        self.assertNotIn("\n", first)
        self.assertNotIn("abcdefghijkl", first)
        self.assertLessEqual(len(second), log.PROSE_LIMIT)
        self.assertTrue(second.startswith("…") and second.endswith("newest"))

    def test_blank_prose_and_tool_output_are_not_entries(self):
        write(
            self.events,
            [
                codex("item.completed", "r1", type="reasoning", text="   "),
                codex(
                    "item.completed",
                    "c1",
                    type="command_execution",
                    command="cat secrets",
                    aggregated_output="SECRET-OUTPUT",
                    exit_code=0,
                ),
            ],
        )
        self.assertEqual([("command", "cat secrets")], self.entries())
        self.assertNotIn("SECRET-OUTPUT", json.dumps(log.read(self.events)))

    def test_only_the_newest_entries_are_kept(self):
        write(self.events, [codex("item.completed", f"m{n}", type="agent_message", text=f"m {n}") for n in range(60)])
        shown = self.entries()
        self.assertEqual(log.LIMIT, len(shown))
        self.assertEqual("m 59", shown[-1][1])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.run = self.workspace / ".autocode" / "runs" / "r1"
        self.events = self.run / "iterations" / "builder-01.jsonl"
        write(self.events, [codex("item.completed", "r1", type="reasoning", text="Thinking about tests")])
        self.state = {
            "active_stage": {"stage": "terra", "events": str(self.events)},
            "current_task": {"milestone_id": "M2"},
            "workspace": str(self.workspace),
        }
        self.public = {"status": "RUNNING", "needs": {"kind": "continue"}, "liveness": {"kind": "supervised"}}
        self.confirmed = True

    def project(self, **flags):
        return log.project(self.state, self.public, self.run, supervised=lambda _: self.confirmed, **flags)

    def worker(self, number, status="RUNNING", **active):
        run_dir = self.workspace / ".autocode" / "builders" / "b1" / str(number) / ".autocode" / "runs" / f"w{number}"
        events = run_dir / "iterations" / "builder-01.jsonl"
        write(events, [codex("item.completed", "r1", type="reasoning", text=f"Worker {number} thinking")])
        child = {"active_stage": {"stage": "terra", "events": str(events), **active}}
        (run_dir / "state.json").write_text(json.dumps(child))
        return {"milestone_id": f"M{number}", "run_dir": str(run_dir), "status": status}

    def test_the_running_agent_is_named_with_its_task(self):
        [entry] = self.project()
        self.assertEqual("Builder · task M2", entry["agent"])
        self.assertEqual(("thinking", "Thinking about tests"), (entry["kind"], entry["text"]))
        self.assertEqual(entry["id"], self.project()[0]["id"], "an entry keeps its id across views")

    def test_an_attempt_that_cannot_be_confirmed_or_is_over_shows_nothing(self):
        self.assertEqual([], self.project(finished=True))
        self.assertEqual([], self.project(stopped=True))
        for kind in ("unknown", "unsupervised", "interrupted", "stopped"):
            self.public["liveness"] = {"kind": kind}
            self.assertEqual([], self.project(), kind)
        self.public.update(status="PAUSED_REQUESTED", liveness={"kind": "supervised"})
        self.assertEqual([], self.project())

    def test_a_file_outside_the_run_directory_is_never_read(self):
        outside = self.workspace / "elsewhere.jsonl"
        write(outside, [codex("item.completed", "r1", type="reasoning", text="secret")])
        self.state["active_stage"]["events"] = str(outside)
        self.assertEqual([], self.project())

    def test_parallel_builders_each_show_their_own_supervised_attempt(self):
        del self.state["active_stage"]
        self.state["orchestration_batch"] = {
            "status": "BUILDING",
            "workers": [self.worker(1), self.worker(2), self.worker(3, status="BUILT")],
        }
        self.assertEqual(
            [("Builder · task M1", "Worker 1 thinking"), ("Builder · task M2", "Worker 2 thinking")],
            [(entry["agent"], entry["text"]) for entry in self.project()],
            "a finished member is not read",
        )
        self.confirmed = False
        self.assertEqual([], self.project(), "a Builder that cannot be confirmed as working shows nothing")

    def test_a_worker_outside_the_workspace_or_with_a_file_outside_its_run_is_never_read(self):
        del self.state["active_stage"]
        row = self.worker(1)
        child_path = Path(row["run_dir"]) / "state.json"
        outside = self.workspace.parent / f"{self.workspace.name}-outside.jsonl"
        write(outside, [codex("item.completed", "r1", type="reasoning", text="secret")])
        self.addCleanup(outside.unlink)
        child_path.write_text(json.dumps({"active_stage": {"stage": "terra", "events": str(outside)}}))
        self.state["orchestration_batch"] = {"status": "BUILDING", "workers": [row]}
        self.assertEqual([], self.project())
        row["run_dir"] = str(self.workspace.parent)
        self.assertEqual([], self.project())

    def test_the_inputs_are_not_changed(self):
        before = json.dumps([self.state, self.public], sort_keys=True)
        self.project()
        self.assertEqual(before, json.dumps([self.state, self.public], sort_keys=True))


class StatusViewTests(unittest.TestCase):
    """`--status` carries ``chat.log`` while a supervised attempt works, and omits it otherwise."""

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

    def test_a_supervised_attempt_shows_its_thinking_beside_the_unchanged_activity(self):
        chat = self.render(
            {},
            [
                codex("item.completed", "r1", type="reasoning", text="Run the tests first"),
                codex("item.started", "c1", type="command_execution", command="pytest -q"),
            ],
        )
        self.assertEqual(
            [("Builder", "thinking", "Run the tests first"), ("Builder", "command", "pytest -q")],
            [(entry["agent"], entry["kind"], entry["text"]) for entry in chat["log"]],
        )
        self.assertEqual(
            [{"kind": "command", "text": "pytest -q", "status": "running"}],
            chat["activity"],
            "chat.activity still never copies reasoning",
        )

    def test_an_attempt_without_supervision_shows_none(self):
        chat = self.render({"keeper": False}, [codex("item.completed", "r1", type="reasoning", text="hidden")])
        self.assertNotIn("log", chat)


if __name__ == "__main__":
    unittest.main()
