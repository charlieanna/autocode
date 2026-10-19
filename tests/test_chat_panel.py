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
        section = panel.sections(shown)[1]
        self.assertEqual("Activity", section.title)
        self.assertEqual(
            [("±", "edited app/greet.py", "plain"), ("$", "pytest -q", "accent"), ("✗", "false", "bad")],
            [(row.mark, row.text, row.style) for row in section.rows],
        )
        many = self.live(*[{"kind": "tool", "text": f"read {n}", "status": "done"} for n in range(12)])
        self.assertEqual(panel.EVENT_ROWS, len(panel.sections(many)[1].rows))
        self.assertEqual(["Now", "Requirements", "Tasks"], [s.title for s in panel.sections(view())])

    def test_the_status_row_names_the_newest_event(self):
        self.assertEqual("Running pytest -q", panel.doing(self.live(self.EDIT, self.COMMAND)))
        self.assertEqual("Editing: edited app/greet.py", panel.doing(self.live(self.COMMAND, self.EDIT)))
        self.assertIsNone(panel.doing(view()))

    def test_malformed_events_are_ignored(self):
        junk = self.live({"kind": "nope", "text": "x"}, {"kind": "command"}, "text", self.COMMAND)
        self.assertEqual([self.COMMAND], panel.events(junk))

    def test_each_event_is_logged_once_and_a_failure_is_logged_when_it_happens(self):
        log = panel.StageLog()
        log.observe(self.live(self.COMMAND), now=0)
        self.assertFalse(log.observe(self.live(self.COMMAND), now=1))
        log.observe(self.live({**self.COMMAND, "status": "done"}, self.EDIT), now=2)
        log.observe(self.live(self.FAILED), now=3)
        log.observe(self.live(self.FAILED), now=4)
        said = [line.split(" ", 1)[1] for line in log.lines]
        events = [line for line in said if line.split(":")[0] in ("Ran", "Edited", "Used", "Failed")]
        self.assertEqual(["Ran: pytest -q", "Edited: edited app/greet.py", "Ran: false", "Failed: false"], events)

    def test_a_view_read_before_the_launched_step_saved_anything_is_not_shown_as_a_request(self):
        waiting = view(headline="Waiting for you", needs="plan approval needed")
        waiting["needs"] = {"kind": "approve_plan"}
        self.assertEqual("plan approval needed", panel.sections(waiting)[0].rows[0].text)
        now = panel.sections(waiting, launched=True)[0]
        self.assertEqual(("Starting the next step…", ()), (now.summary, now.rows))
        self.assertEqual("Builder working", panel.sections(view(), launched=True)[0].summary)

    def test_the_same_command_run_twice_is_logged_twice(self):
        log = panel.StageLog()
        done = {**self.COMMAND, "status": "done"}
        log.observe(self.live(done), now=0)
        log.observe(self.live(done, done), now=1)
        log.observe(self.live(done, done), now=2)
        ran = [line for line in log.lines if "Ran:" in line]
        self.assertEqual(2, len(ran), log.lines)

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


if __name__ == "__main__":
    unittest.main()
