"""The full-screen chat's side panel, stage log and `/` commands, from status views alone."""

import unittest

import autocode_chat_panel as panel


def view(*, headline="Builder working", needs="nothing needed from you", task="working", requirement="unchecked"):
    return {
        "status": "RUNNING",
        "progress": {
            "headline": headline,
            "needs_you": needs,
            "requirements": {
                "label": "0 of 1 requirement checked",
                "items": [{"id": "C1", "label": "Greets by name", "state": requirement}],
            },
            "tasks": {"label": "0 of 1 task done", "items": [{"number": 1, "label": "Write greet.py", "state": task}]},
            "problems": {"open": 0, "items": []},
        },
        "explanation": {"explanation": "AutoCode is building the first task."},
    }


class SectionTests(unittest.TestCase):
    def test_a_working_run_shows_where_it_is_its_requirements_and_tasks(self):
        shown = panel.sections(view())
        self.assertEqual(["Now", "Requirements", "Tasks"], [section.title for section in shown])
        self.assertEqual("Builder working", shown[0].summary)
        self.assertEqual((), shown[0].rows, "nothing needed from you is not shown as a need")
        self.assertEqual(panel.Row("·", "C1 Greets by name", "plain"), shown[1].rows[0])
        self.assertEqual(panel.Row("▸", "1. Write greet.py", "accent"), shown[2].rows[0])

    def test_a_need_failed_requirement_and_open_problems_stand_out(self):
        current = view(needs="plan approval needed", requirement="failed")
        current["progress"]["problems"] = {"open": 1, "items": [{"id": "F1", "finding": "usage goes to stdout"}]}
        shown = {section.title: section for section in panel.sections(current)}
        self.assertEqual(panel.Row("!", "plan approval needed", "warn"), shown["Now"].rows[0])
        self.assertEqual("bad", shown["Requirements"].rows[0].style)
        self.assertEqual(
            ("1 open", "usage goes to stdout"), (shown["Problems"].summary, shown["Problems"].rows[0].text)
        )

    def test_a_view_without_progress_shows_only_its_status(self):
        self.assertEqual([panel.Section("Now", "WAITING FOR USER")], panel.sections({"status": "WAITING_FOR_USER"}))
        self.assertEqual([panel.Section("Now", "Starting")], panel.sections(None))


class CommandTests(unittest.TestCase):
    def test_pause_only_while_autocode_works_and_quit_only_at_a_question(self):
        asking = [command.name for command in panel.commands(asking=True)]
        working = [command.name for command in panel.commands(asking=False)]
        self.assertIn("/quit", asking)
        self.assertNotIn("/pause", asking)
        self.assertIn("/pause", working)
        self.assertNotIn("/quit", working)

    def test_a_typed_prefix_matches_in_menu_order(self):
        available = panel.commands(asking=False)
        self.assertEqual(["/panel", "/pause"], [command.name for command in panel.matching("/pa", available)])
        self.assertEqual([], panel.matching("/x", available))

    def test_the_commands_print_what_the_view_and_log_say(self):
        log = panel.StageLog()
        log.observe(view(), now=0)
        self.assertEqual("0 of 1 requirement checked\n· C1 Greets by name", panel.text("/plan", view(), log))
        self.assertEqual("0 of 1 task done\n▸ 1. Write greet.py", panel.text("/tasks", view(), log))
        self.assertIn("Builder working", panel.text("/log", view(), log))
        self.assertEqual("AutoCode is building the first task.", panel.text("/details", view(), log))
        self.assertIn("/pause", panel.text("/help", view(), log))
        available = panel.commands(asking=False, client=False, run=False)
        help_text = panel.text("/help", view(), log, available=available)
        self.assertNotIn("/live", help_text)
        self.assertNotIn("/log", help_text)
        self.assertIn("/help", help_text)
        self.assertEqual("There is no plan yet.", panel.text("/plan", {}, log))


class ActivityTests(unittest.TestCase):
    def activity_view(self):
        shown = view()
        shown["progress"]["tasks"]["total"] = 1
        shown["chat"] = {
            "progress": [
                "Builder working · task M1",
                "0 of 1 task done",
                "Recent steps: Builder attempt finished · Tester attempt needs correction",
            ]
        }
        return shown

    def test_now_shows_the_current_step_and_recent_attempts_but_not_the_counts_again(self):
        now = panel.sections(self.activity_view())[0]
        self.assertEqual("Builder working · task M1", now.summary)
        self.assertEqual(
            ["Recent steps: Builder attempt finished · Tester attempt needs correction"], [row.text for row in now.rows]
        )

    def test_the_log_records_each_new_attempt_line(self):
        log = panel.StageLog()
        log.observe(self.activity_view(), now=0)
        later = self.activity_view()
        later["chat"]["progress"][2] = "Recent steps: Tester attempt needs correction · Builder attempt finished"
        log.observe(later, now=1)
        said = [line.split(" ", 1)[1] for line in log.lines]
        self.assertEqual("Builder working · task M1", said[0])
        self.assertIn("Tester attempt needs correction", said, "the newest attempt, not the whole line")
        self.assertEqual("Builder attempt finished", said[-1], said)


class StepTests(unittest.TestCase):
    def steps_view(self, blocked=None):
        actions = [{"id": "compare"}] + ([] if blocked else [{"id": "restore"}])
        return {
            "code_checkpoints": {
                "blocked_reason": blocked,
                "rows": [
                    {"id": "s1", "label": "Builder step for M1", "changed_files": ["greet.py"], "actions": actions},
                    {"id": "s2", "label": "Old step", "changed_files": [], "actions": [], "reason": "not stored"},
                ],
            }
        }

    def test_the_steps_are_numbered_from_one_with_their_files(self):
        self.assertEqual(
            "1. Builder step for M1\n   greet.py\n2. Old step (unavailable: not stored)\n"
            "Type /diff N to see what changed since step N.",
            panel.steps_text(self.steps_view(), "/diff"),
        )
        self.assertIn("Type /undo N", panel.steps_text(self.steps_view(), "/undo"))
        self.assertIn(
            "Going back is not possible now: a step is running",
            panel.steps_text(self.steps_view("a step is running"), "/undo"),
        )
        self.assertIn("no saved code steps", panel.steps_text({}, "/diff"))

    def test_a_comparison_lists_the_files_then_the_diff(self):
        shown = panel.comparison_text({"changed_files": ["a.py"], "patch": "--- a\n+++ b", "truncated": True})
        self.assertEqual(
            "1 file changed since that step:\n  a.py\n\n--- a\n+++ b\n"
            "(The diff is longer than this; the full changes are in the run's branch.)",
            shown,
        )
        self.assertIn(
            "Nothing has changed since step 2: the files match it.", panel.comparison_text({"changed_files": []}, 2)
        )
        self.assertIn("1 file changed since step 2:", panel.comparison_text({"changed_files": ["a.py"]}, "2"))

    def test_undo_only_at_a_question_and_client_commands_only_with_a_client(self):
        self.assertIn("/undo", [command.name for command in panel.commands(asking=True)])
        self.assertNotIn("/undo", [command.name for command in panel.commands(asking=False)])
        self.assertIn("/diff", [command.name for command in panel.commands(asking=False)])
        bare = [command.name for command in panel.commands(asking=True, client=False)]
        self.assertNotIn("/diff", bare)
        self.assertNotIn("/undo", bare)


class LiveEventTests(unittest.TestCase):
    def live(self, *events, needs="nothing needed from you", kind="continue"):
        shown = view(headline="Builder working", needs=needs)
        shown["needs"] = {"kind": kind}
        shown["chat"] = {"activity": list(events)}
        return shown

    COMMAND = {"kind": "command", "text": "pytest -q", "status": "running"}
    EDIT = {"kind": "edit", "text": "edited app/greet.py", "paths": ["app/greet.py"], "status": "done"}
    FAILED = {"kind": "command", "text": "false", "status": "failed"}

    def test_the_panel_lists_the_newest_events_under_now_with_their_marks(self):
        shown = self.live(self.EDIT, self.COMMAND, self.FAILED)
        sections = panel.sections(shown)
        section = sections[1]
        self.assertEqual("Activity", section.title)
        self.assertEqual(
            [("±", "edited app/greet.py", "plain")],
            [(row.mark, row.text, row.style) for row in section.rows],
        )
        self.assertNotIn("pytest -q", repr(sections))
        self.assertNotIn("false", repr(sections))
        many = self.live(*[{"kind": "tool", "text": f"read {n}", "status": "done"} for n in range(12)])
        self.assertEqual(panel.EVENT_ROWS, len(panel.sections(many)[1].rows))
        self.assertEqual(["Now", "Requirements", "Tasks"], [s.title for s in panel.sections(view())])

    def test_the_status_row_names_the_newest_event(self):
        self.assertIsNone(panel.doing(self.live(self.EDIT, self.COMMAND)))
        self.assertEqual("Editing: edited app/greet.py", panel.doing(self.live(self.COMMAND, self.EDIT)))
        self.assertIsNone(panel.doing(view()))

    def test_malformed_events_are_ignored(self):
        junk = self.live({"kind": "nope", "text": "x"}, {"kind": "command"}, "text", self.COMMAND)
        self.assertEqual([], panel.events(junk))

    def test_only_non_command_events_are_logged(self):
        log = panel.StageLog()
        log.observe(self.live(self.COMMAND), now=0)
        self.assertFalse(log.observe(self.live(self.COMMAND), now=1))
        log.observe(self.live({**self.COMMAND, "status": "done"}, self.EDIT), now=2)
        log.observe(self.live(self.FAILED), now=3)
        log.observe(self.live(self.FAILED), now=4)
        self.assertTrue(any("Edited: edited app/greet.py" in line for line in log.lines))
        self.assertNotIn("pytest -q", "\n".join(log.lines))
        self.assertNotIn("false", "\n".join(log.lines))

    def test_a_view_read_before_the_launched_step_saved_anything_is_not_shown_as_a_request(self):
        waiting = view(headline="Waiting for you", needs="plan approval needed")
        waiting["needs"] = {"kind": "approve_plan"}
        self.assertEqual("plan approval needed", panel.sections(waiting)[0].rows[0].text)
        now = panel.sections(waiting, launched=True)[0]
        self.assertEqual(("Starting the next step…", ()), (now.summary, now.rows))
        self.assertEqual("Builder working", panel.sections(view(), launched=True)[0].summary)

    def test_commands_are_not_logged(self):
        log = panel.StageLog()
        done = {**self.COMMAND, "status": "done"}
        log.observe(self.live(done), now=0)
        log.observe(self.live(done, done), now=1)
        log.observe(self.live(done, done), now=2)
        self.assertNotIn("pytest -q", "\n".join(log.lines))

    def test_a_run_that_continues_by_itself_needs_nobody(self):
        words = "AutoResolver must reconcile retained attempt 001/builder-01 before any provider call"
        shown = self.live(needs=words)
        self.assertIsNone(panel.needs_you(shown))
        self.assertEqual([], [row for row in panel.sections(shown)[0].rows if row.mark == "!"])
        log = panel.StageLog()
        log.observe(shown, now=0)
        self.assertNotIn("Needs you", "\n".join(log.lines))
        self.assertEqual(words, panel.needs_you(self.live(needs=words, kind="approve_plan")))


class StageLogTests(unittest.TestCase):
    def test_each_change_is_logged_once(self):
        log = panel.StageLog()
        self.assertTrue(log.observe(view(), now=0))
        self.assertFalse(log.observe(view(), now=1), "an unchanged view adds nothing")
        self.assertTrue(log.observe(view(headline="Tester working", task="done"), now=2))
        self.assertTrue(log.observe(view(headline="Tester working", task="done", requirement="checked"), now=3))
        self.assertTrue(
            log.observe(
                view(headline="Waiting for you", needs="plan approval needed", task="done", requirement="checked"),
                now=4,
            )
        )
        said = [line.split(" ", 1)[1] for line in log.lines]
        self.assertEqual(
            [
                "Builder working",
                "Task 1 started",
                "Tester working",
                "Task 1 done",
                "C1 checked",
                "Waiting for you",
                "Needs you: plan approval needed",
            ],
            said,
        )

    def test_a_view_without_progress_is_not_a_change(self):
        log = panel.StageLog()
        self.assertFalse(log.observe({"status": "RUNNING"}))
        self.assertEqual([], log.lines)


class LiveLogTests(unittest.TestCase):
    """The live log keeps what each agent did across views; the view holds only the newest entries."""

    # A Claude stage's progress notes as the live run of 2026-11-02 showed them: one growing note.
    NOTES = "…king ~300 tokens; thinking ~450 tokens; thinking ~600 tokens; writing StructuredOutput input: 6774 chars;"

    def test_progress_notes_read_as_their_newest_value_once(self):
        self.assertEqual("thought ~600 tokens · writing its answer · 6.8k chars", panel.live_text(self.NOTES))
        self.assertEqual("thought ~950 tokens", panel.live_text("thinking ~950 tokens"))
        self.assertEqual(
            "thought ~1.2k tokens · writing Edit · 40 chars · ran ls",
            panel.live_text("thinking ~1200 tokens; writing Edit input: 40 chars; ran ls"),
        )

    def test_the_models_own_words_are_shown_as_they_are(self):
        for words in ("Read greet.py first; then run the tests.", "thinking about ~3 options", ""):
            self.assertEqual(words, panel.live_text(words))

    def test_the_log_text_the_conversation_prints_is_condensed_too(self):
        live = panel.LiveLog()
        live.observe(self.view(("a:r1", "Tester", "thinking", self.NOTES)))
        self.assertIn("Tester ∴ thought ~600 tokens · writing its answer · 6.8k chars", live.text())
        self.assertNotIn("thinking ~450", live.text())

    @staticmethod
    def view(*entries):
        return {"chat": {"log": [dict(zip(("id", "agent", "kind", "text"), entry, strict=True)) for entry in entries]}}

    def test_entries_are_kept_across_views_and_a_growing_thought_is_updated_in_place(self):
        live = panel.LiveLog()
        self.assertTrue(live.observe(self.view(("a:r1", "Builder", "thinking", "Look at"))))
        self.assertTrue(
            live.observe(
                self.view(("a:r1", "Builder", "thinking", "Look at greet.py"), ("a:c1", "Builder", "command", "ls"))
            )
        )
        self.assertFalse(live.observe(self.view(("a:c1", "Builder", "command", "ls"))), "command is not exposed")
        self.assertEqual(["Look at greet.py"], [entry["text"] for entry in live.shown()])

    def test_malformed_rows_are_ignored(self):
        live = panel.LiveLog()
        live.observe({"chat": {"log": [{"id": "x", "agent": "Builder", "kind": "secret", "text": "t"}, "row", None]}})
        self.assertEqual({}, live.entries)

    def test_the_filter_keeps_one_agent_or_one_kind(self):
        live = panel.LiveLog()
        live.observe(
            self.view(
                ("a:r1", "Builder · task M1", "thinking", "one"),
                ("b:c1", "Builder · task M2", "command", "two"),
                ("c:m1", "Plan Reviewer", "message", "three"),
            )
        )
        self.assertEqual(["Builder · task M1", "Plan Reviewer"], live.agents())
        live.filter = "m2"
        self.assertEqual([], live.shown())
        live.filter = "thinking"
        self.assertEqual(["one"], [entry["text"] for entry in live.shown()])
        live.filter = "tools"
        self.assertEqual([], live.shown())
        self.assertEqual("LIVE · tools", live.title())

    def test_only_the_newest_entries_are_kept(self):
        live = panel.LiveLog()
        for number in range(panel.LIVE_LIMIT + 5):
            live.observe(self.view((f"a:{number}", "Builder", "message", str(number))))
        self.assertEqual(panel.LIVE_LIMIT, len(live.entries))
        self.assertEqual(str(panel.LIVE_LIMIT + 4), live.shown()[-1]["text"])

    def test_live_is_offered_once_a_chat_is_open(self):
        self.assertIn("/live", [command.name for command in panel.commands(asking=False)])
        self.assertNotIn("/live", [command.name for command in panel.commands(asking=True, run=False)])


if __name__ == "__main__":
    unittest.main()


class RunListTests(unittest.TestCase):
    """What /runs lists: this project's chats, newest first, from `autocode runs --json` rows."""

    ROWS = [
        {
            "run_dir": "/p/.autocode/runs/a",
            "title": "Fix the typo",
            "status": "TASK_COMPLETE",
            "updated_at": "2026-10-19T13:00:00",
        },
        {
            "run_dir": "/p/.autocode/runs/b",
            "name": "b",
            "bucket": "needs you",
            "pause_category_label": "Plan approval",
            "updated_at": "2026-10-19T14:00:00",
        },
        {"run_dir": "/p/.autocode/runs/c", "name": "c", "availability": "unreadable", "reason": "bad json"},
    ]

    def test_newest_first_with_each_chats_request_and_state(self):
        rows = panel.run_rows(self.ROWS)
        self.assertEqual(["b", "a", "c"], [row["run_dir"][-1] for row in rows])
        text = panel.runs_text(rows, current="/p/.autocode/runs/a")
        self.assertIn("  1. b\n     needs you: Plan approval · 2026-10-19 14:00", text)
        self.assertIn("  2. Fix the typo  (this chat)\n     complete · 2026-10-19 13:00", text)
        self.assertIn("  3. c\n     cannot be read", text)
        self.assertTrue(text.endswith("Type /open N to open one, or /new to start another."))

    def test_an_empty_list_says_how_to_start(self):
        self.assertEqual("No chats yet. Type a request to start one.", panel.runs_text([]))

    def test_chats_are_grouped_by_project_this_one_first_then_by_latest_activity(self):
        def chat(workspace, name, at, **more):
            return {
                "run_dir": f"{workspace}/.autocode/runs/{name}",
                "workspace": workspace,
                "title": name,
                "status": "TASK_COMPLETE",
                "updated_at": at,
                **more,
            }

        listed = [
            chat("/w/old", "o1", "2026-10-01T10:00:00"),
            chat("/w/here", "h1", "2026-10-02T10:00:00"),
            chat("/w/recent", "r1", "2026-10-18T10:00:00"),
            chat("/w/here", "h2", "2026-10-03T10:00:00"),
            chat("/w/recent", "r2", "2026-10-19T10:00:00"),
            chat("/w/old", "gone", "", availability="checkpoint_missing"),
            chat("/w/here", "h2", "2026-10-03T10:00:00"),  # listed twice: this project and the registry
        ]
        rows = panel.run_rows(listed, here="/w/here")
        self.assertEqual(["h2", "h1", "r2", "r1", "o1"], [row["title"] for row in rows])
        self.assertEqual(1, panel.hidden(listed, "/w/here"))
        text = panel.runs_text(rows, here="/w/here", left_out=1)
        self.assertTrue(text.startswith("here  /w/here  (this project)\n  1. h2"))
        self.assertIn("\n\nrecent  /w/recent\n  3. r2", text)
        self.assertIn("\n\nold  /w/old\n  5. o1", text)
        self.assertIn("(1 more in other projects cannot be opened: their runs were removed.)", text)

    def test_before_a_chat_is_open_only_what_works_without_one_is_offered(self):
        names = [command.name for command in panel.commands(asking=True, run=False)]
        self.assertEqual(
            [
                "/runs",
                "/open",
                "/new",
                "/mode",
                "/engine",
                "/connect",
                "/stages",
                "/repos",
                "/attention",
                "/help",
                "/quit",
            ],
            names,
        )

    def test_opening_and_starting_a_chat_wait_for_a_question(self):
        working = [command.name for command in panel.commands(asking=False)]
        asking = [command.name for command in panel.commands(asking=True)]
        self.assertIn("/runs", working)
        self.assertNotIn("/open", working)
        self.assertNotIn("/new", working)
        self.assertIn("/open", asking)
        self.assertIn("/new", asking)
