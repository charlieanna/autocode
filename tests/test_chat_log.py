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


def progress(kind="reasoning", identifier="p1", position=23, **changes):
    value = {
        "id": identifier,
        "kind": kind,
        "position": position,
        "nonwhite": True,
        "content_hash": "a" * 64,
        "delta_hash": "b" * 64,
    }
    value.update(changes)
    return {"type": "autocode_progress", "version": 1, "progress": value}


class ReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.events = Path(temp.name) / "builder-01.jsonl"

    def entries(self):
        return [(entry["kind"], entry["text"]) for _, entry in log.read(self.events, "/work")]

    def test_structured_response_prefixes_are_distinct_from_readable_prose(self):
        for text in (
            "{",
            '{"summary":',
            ' { "summary": "checked" }',
            '```json\n{"summary": "checked"}\n```',
            '```\n{"summary": "checked"}\n```',
        ):
            with self.subTest(text=text):
                self.assertTrue(log._structured_response(text))
        for text in (
            "",
            "Which codes should history show?",
            "[checking] the registry mappings",
            'The response contains {"code": 0}.',
            "{operation} identifies the request",
            "```go\nfunc check() {}\n```",
            '{"code": 0} Which result should this show?',
        ):
            with self.subTest(text=text):
                self.assertFalse(log._structured_response(text))

    def test_reasoning_and_questions_are_not_reclassified_as_reports(self):
        text = '{"code": 0} Which result should this show?'
        write(
            self.events,
            [
                codex("item.completed", "question", type="agent_message", text=text),
                codex("item.completed", "thought", type="reasoning", text='{"code": 0}'),
            ],
        )
        self.assertEqual([("message", text), ("thinking", '{"code": 0}')], self.entries())

    def test_json_report_is_a_neutral_update_without_displacing_readable_messages(self):
        report = json.dumps({"summary": "saved " * 200, "workspace_scope": {"spans_repositories": True}})
        for row in (
            codex("item.completed", "report", type="agent_message", text=report),
            {"type": "text", "part": {"id": "report", "text": report}},
        ):
            with self.subTest(row_type=row["type"]):
                write(
                    self.events,
                    [
                        codex(
                            "item.completed",
                            "update",
                            type="agent_message",
                            text="Checking the existing result mappings.",
                        ),
                        row,
                        progress("text", "report"),
                    ],
                )
                original = self.events.read_bytes()
                self.assertEqual(
                    [
                        ("message", "Checking the existing result mappings."),
                        ("message", "Structured response received."),
                    ],
                    self.entries(),
                )
                self.assertEqual(original, self.events.read_bytes(), "raw report evidence stays intact")

    def test_a_fenced_report_after_prose_shows_the_prose_not_the_report(self):
        # A live Plan Reviewer on Kilo ended its summary with a fenced JSON report, and the log
        # showed the report's last 400 characters instead of the summary.
        summary = "I've verified the current code and recomputed every worked example; one finding remains."
        report = "```json\n" + json.dumps({"verdict": "REVISE", "notes": "saved " * 200}, indent=2) + "\n```"
        example = summary + '\n```json\n{"code": 0}\n```\nWhich result should this show?'
        write(
            self.events,
            [
                codex("item.completed", "report", type="agent_message", text=summary + "\n\n" + report),
                codex("item.updated", "streaming", type="agent_message", text=summary + '\n```json\n{"verdict": "RE'),
                codex("item.completed", "example", type="agent_message", text=example),
            ],
        )
        self.assertEqual(
            [
                ("message", summary + " Structured response received."),
                ("message", summary + " Receiving a structured response. [partial]"),
                ("message", log._prose(example)),
            ],
            self.entries(),
        )

    def test_streaming_json_replaces_one_entry_and_keeps_its_partial_notice(self):
        rows = [
            codex("item.updated", "report", type="agent_message", text="{"),
            codex("item.updated", "report", type="agent_message", text='{"summary": "checking'),
        ]
        write(self.events, rows)
        before = log.read(self.events)
        self.assertEqual([("message", "Receiving a structured response. [partial]")], self.entries())
        rows.append(codex("item.completed", "report", type="agent_message", text='{"summary": "checked"}'))
        write(self.events, rows)
        after = log.read(self.events)
        self.assertEqual(before[0][0], after[0][0])
        self.assertEqual([("message", "Structured response received.")], self.entries())

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
                ("thinking", "I should look at greet.py first [partial]"),
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

    def test_hashed_streaming_progress_shows_activity_without_inventing_prose(self):
        write(self.events, [progress(), progress(position=50), progress("text", "p2")])
        self.assertEqual(
            [
                ("thinking", "Reasoning stream activity received; text unavailable."),
                ("message", "Response stream activity received; text unavailable."),
            ],
            self.entries(),
        )
        entries = log.read(self.events)
        self.assertEqual(["streaming", "streaming"], [row["status"] for _, row in entries])
        self.assertNotIn("a" * 64, json.dumps(entries))
        self.assertNotIn("b" * 64, json.dumps(entries))

    def test_a_kilo_reasoning_preview_shows_the_newest_thinking_and_a_response_stays_opaque(self):
        # 2026-11-05: on Kilo the live log said only "text unavailable", so a person could not tell
        # what the Investigator was doing. The hook now previews reasoning; a response never.
        write(
            self.events,
            [
                progress(preview="Checking slugify.py: the regex turns the outer spaces into hyphens"),
                progress(
                    position=90,
                    preview="Now probing slugify('  Hello, World!  ') api_key=sk-abcdefghijklmnopqrstuvwxyz123456",
                ),
                progress("text", "p2", preview='{"outcome": "reproduced"}'),
            ],
        )
        entries = self.entries()
        self.assertEqual("thinking", entries[0][0])
        self.assertTrue(entries[0][1].startswith("Now probing slugify(' Hello, World! ')"), entries)
        self.assertTrue(entries[0][1].endswith("[partial]"))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", entries[0][1], "redacted like other prose")
        self.assertEqual(("message", "Response stream activity received; text unavailable."), entries[1])
        self.assertNotIn("reproduced", json.dumps(log.read(self.events)))

    def test_a_blank_or_malformed_preview_keeps_the_opaque_receipt(self):
        for preview in ("   ", 7, None, ["text"]):
            with self.subTest(preview=preview):
                write(self.events, [progress(preview=preview)])
                self.assertEqual(
                    [("thinking", "Reasoning stream activity received; text unavailable.")], self.entries()
                )

    def test_a_run_of_identical_receipts_becomes_one_line_that_counts_the_parts(self):
        """#1999: opaque receipts must not bury the tool calls the live log exists to show."""
        write(
            self.events,
            [
                progress("reasoning", "p1"),
                progress("reasoning", "p2", position=40),
                progress("reasoning", "p3", position=60),
                progress("text", "p4"),
                {
                    "type": "tool_use",
                    "part": {
                        "id": "p5",
                        "tool": "bash",
                        "state": {"status": "completed", "input": {"command": "pytest -q"}},
                    },
                },
            ],
        )
        self.assertEqual(
            [
                ("thinking", "Reasoning stream activity received; text unavailable. (3 parts streamed)"),
                ("message", "Response stream activity received; text unavailable."),
                ("command", "pytest -q"),
            ],
            self.entries(),
        )

    def test_one_receipt_keeps_its_wording_and_real_prose_is_never_counted(self):
        write(
            self.events,
            [
                progress("reasoning", "p1"),
                {"type": "reasoning", "part": {"id": "p2", "type": "reasoning", "text": "Reading greet.py"}},
                {"type": "reasoning", "part": {"id": "p3", "type": "reasoning", "text": "Now editing it"}},
            ],
        )
        self.assertEqual(
            [
                ("thinking", "Reasoning stream activity received; text unavailable."),
                ("thinking", "Reading greet.py"),
                ("thinking", "Now editing it"),
            ],
            self.entries(),
            "text the provider really sent is shown part by part, unchanged",
        )

    def test_actual_prose_replaces_progress_and_late_progress_cannot_hide_it(self):
        write(
            self.events,
            [
                progress(),
                {"type": "reasoning", "part": {"id": "p1", "text": "Now checking the original tests."}},
                progress(position=80),
            ],
        )
        self.assertEqual([("thinking", "Now checking the original tests.")], self.entries())
        self.assertNotIn("status", log.read(self.events)[0][1])

    def test_invalid_blank_and_unsupported_progress_are_not_activity(self):
        variants = [
            progress("tool"),
            progress(nonwhite=False),
            progress(position=0),
            progress(position=True),
            progress(position="1"),
            progress(identifier=None),
            progress(identifier=[]),
            progress(identifier=True),
            {"type": "autocode_progress", "progress": None},
        ]
        write(self.events, variants)
        self.assertEqual([], self.entries())

    def test_progress_never_copies_arbitrary_text_or_tool_output(self):
        write(self.events, [progress(text="api_key=abcdefghijkl", output="SECRET-OUTPUT")])
        shown = json.dumps(log.read(self.events))
        self.assertIn("Reasoning stream activity received", shown)
        self.assertNotIn("abcdefghijkl", shown)
        self.assertNotIn("SECRET-OUTPUT", shown)

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
        self.assertTrue(second.startswith("old old ") and second.endswith(" old…"), second)

    def test_a_finished_thought_keeps_its_first_words_and_ends_where_it_was_cut(self):
        # A thought's start is what reads once it is complete; the newest words matter only while it
        # streams. The cut was marked "[earlier text omitted]" at the front, which read as part of
        # the thought on every thought longer than the bound (2026-11-08 live run).
        text = "Which codes should the history show? " + "older context " * 50
        shown = log._prose(text)
        self.assertTrue(shown.startswith("Which codes should the history show? older context"))
        self.assertTrue(shown.endswith("…") and text.startswith(shown[:-1] + " "), "cut at a word boundary")
        self.assertLessEqual(len(shown), log.PROSE_LIMIT)
        self.assertNotIn("omitted", shown)
        self.assertEqual("x" * (log.PROSE_LIMIT - 1) + "…", log._prose("x" * 500), "one long word is cut as is")

    def test_a_streaming_thought_keeps_its_newest_words_behind_an_ellipsis(self):
        text = "older context " * 50 + "Which codes should the history show?"
        shown = log._prose(text, streaming=True)
        self.assertTrue(shown.startswith("…older context "), shown)
        self.assertTrue(shown.endswith("Which codes should the history show? [partial]"))
        self.assertIn(" " + shown[1:].removesuffix(" [partial]"), text, "a whole word follows the cut")
        self.assertLessEqual(len(shown), log.PROSE_LIMIT)
        self.assertEqual("Short. [partial]", log._prose("Short.", streaming=True))
        self.assertEqual("", log._prose("", streaming=True), "nothing to show is not partial")

    def test_an_unfinished_first_token_is_activity_not_a_finished_word(self):
        write(
            self.events,
            [
                codex("item.updated", "r1", type="reasoning", text="Conf"),
                codex("item.updated", "m1", type="agent_message", text="I’ll"),
                codex("item.completed", "m2", type="agent_message", text="Done"),
            ],
        )
        self.assertEqual(
            [("thinking", "Partial thought received."), ("message", "Partial message received."), ("message", "Done")],
            self.entries(),
        )
        write(
            self.events,
            [
                codex("item.updated", "r1", type="reasoning", text="Conf"),
                codex("item.updated", "r1", type="reasoning", text="Confirm the behavior first"),
            ],
        )
        self.assertEqual([("thinking", "Confirm the behavior first [partial]")], self.entries())

    def test_unfinished_multiword_prose_is_marked_until_its_completed_snapshot(self):
        for kind in ("reasoning", "agent_message"):
            for event in ("item.started", "item.updated"):
                with self.subTest(kind=kind, event=event):
                    rows = [codex(event, "part", type=kind, text="Found an")]
                    write(self.events, rows)
                    before = log.read(self.events)
                    self.assertEqual("Found an [partial]", before[0][1]["text"])
                    rows.append(codex("item.completed", "part", type=kind, text="Found an issue."))
                    write(self.events, rows)
                    after = log.read(self.events)
                    self.assertEqual(before[0][0], after[0][0])
                    self.assertEqual(1, len(after))
                    self.assertEqual("Found an issue.", after[0][1]["text"])

    def test_partial_notice_is_bounded_redacted_and_survives_a_turn_without_final_text(self):
        write(
            self.events,
            [
                codex("item.updated", "part", type="reasoning", text="old " * 200 + "api_key=abcdefghijkl newest"),
                {"type": "turn.completed"},
            ],
        )
        shown = self.entries()[0][1]
        self.assertTrue(shown.startswith("…old "), shown)
        self.assertTrue(shown.endswith("newest [partial]"))
        self.assertLessEqual(len(shown), log.PROSE_LIMIT)
        self.assertNotIn("abcdefghijkl", shown)

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

    def test_the_orchestrators_newest_dispatch_since_the_turn_is_one_message(self):
        # It runs no model and finishes at once, so the log never showed that it ran.
        self.state["turns"] = [{"at": "2026-11-05T10:38:00Z"}]
        dispatch = {"stage": "orchestrator", "runner_owned": True, "summary": "One Builder takes the next task"}
        self.state["stages"] = [
            {**dispatch, "finished_at": "2026-11-05T10:30:00Z", "summary": "Before the turn"},
            {**dispatch, "finished_at": "2026-11-05T10:39:29Z"},
            {"stage": "terra", "started_at": "2026-11-05T10:39:30Z"},
        ]
        note, builder = self.project()
        self.assertEqual(
            {
                "id": "orchestrator:2026-11-05T10:39:29Z",
                "agent": "Orchestrator",
                "kind": "message",
                "text": "One Builder takes the next task",
            },
            note,
        )
        self.assertEqual("Builder · task M2", builder["agent"])
        self.state["turns"].append({"at": "2026-11-05T10:40:00Z"})
        self.assertEqual(["Builder · task M2"], [entry["agent"] for entry in self.project()])

    def test_a_review_below_the_saved_level_says_so_before_its_own_entries(self):
        # Owner decision 2026-11-05: a small, clear task's review runs one level below the stage choice.
        lowered = {"level": "medium", "ceiling": "high", "lowered": True, "reason": "small_clear"}
        self.state["active_stage"].update(
            stage="astra_challenge", started_at="2026-11-05T11:35:55Z", effort_choice=lowered
        )
        note, thought = self.project()
        self.assertEqual(
            {
                "id": "effort:2026-11-05T11:35:55Z",
                "agent": "Plan Reviewer",
                "kind": "message",
                "text": "Running at medium, one level below your high: the task is small and clear.",
            },
            note,
        )
        self.assertEqual(("Plan Reviewer", "thinking"), (thought["agent"], thought["kind"]))
        self.state["active_stage"]["effort_choice"] = {**lowered, "level": "high", "lowered": False}
        self.assertEqual(["thinking"], [entry["kind"] for entry in self.project()], "a kept level says nothing")

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

    def test_supervised_hashed_kilo_progress_waits_for_the_thinking_view(self):
        import autocode_chat_panel as panel

        chat = self.render({}, [progress()])
        self.assertIn("log", chat)
        live = panel.LiveLog()
        self.assertTrue(live.observe({"chat": chat}))
        self.assertIn("Builder [thinking] Reasoning stream activity received", live.text())
        live.filter = "thinking"
        self.assertIn("Builder", live.text())
        self.assertIn("Reasoning stream activity received", live.text())
        self.assertNotIn("No agent is working", live.text())
        self.assertNotIn("activity", chat)
        self.assertNotIn("log", self.render({"keeper": False}, [progress()]))

    def test_an_attempt_without_supervision_shows_none(self):
        chat = self.render({"keeper": False}, [codex("item.completed", "r1", type="reasoning", text="hidden")])
        self.assertNotIn("log", chat)

    def test_public_live_log_shows_readable_update_instead_of_raw_report(self):
        chat = self.render(
            {},
            [
                codex("item.completed", "update", type="agent_message", text="Checking the existing result mappings."),
                {
                    "type": "text",
                    "part": {"id": "report", "text": json.dumps({"workspace_scope": {"spans_repositories": True}})},
                },
            ],
        )
        self.assertEqual(
            ["Checking the existing result mappings.", "Structured response received."],
            [row["text"] for row in chat["log"]],
        )


if __name__ == "__main__":
    unittest.main()
