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
        self.assertEqual("Nothing has changed since that step.", panel.comparison_text({"changed_files": []}))

    def test_undo_only_at_a_question_and_client_commands_only_with_a_client(self):
        self.assertIn("/undo", [command.name for command in panel.commands(asking=True)])
        self.assertNotIn("/undo", [command.name for command in panel.commands(asking=False)])
        self.assertIn("/diff", [command.name for command in panel.commands(asking=False)])
        bare = [command.name for command in panel.commands(asking=True, client=False)]
        self.assertNotIn("/diff", bare)
        self.assertNotIn("/undo", bare)


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
