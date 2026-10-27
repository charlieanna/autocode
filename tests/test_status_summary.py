"""The one-sentence status summary (#2049): which state a public view reads as, and what it offers."""

import unittest

import autocode_status_summary as summary


def view(status, *, needs=None, done=False, headline=None, needs_you=None, next_command=None, stop_reason=None):
    return {
        "status": status,
        "needs": needs,
        "done": done,
        "next_command": next_command,
        "stop_reason": stop_reason,
        "pause_category_label": None,
        "progress": {"headline": headline, "needs_you": needs_you},
    }


class StatusSummaryTests(unittest.TestCase):
    def test_a_question_reads_as_needing_you_with_the_views_own_command(self):
        result = summary.project(
            view(
                "PAUSED_UNANSWERED_QUESTION",
                needs={"kind": "answer"},
                needs_you="2 questions to answer",
                next_command="autocode --answer Q1=ANSWER",
            )
        )
        self.assertEqual(
            {
                "state": "needs_you",
                "label": "Needs you",
                "sentence": "2 questions to answer.",
                "next_command": "autocode --answer Q1=ANSWER",
            },
            result,
        )
        self.assertEqual("Needs you: 2 questions to answer.\nNext: autocode --answer Q1=ANSWER", summary.text(result))

    def test_a_plan_waiting_for_approval_needs_you_even_before_its_token_is_saved(self):
        result = summary.project(
            view("AWAITING_GOAL_APPROVAL", needs={"kind": "continue"}, needs_you="plan approval needed")
        )
        self.assertEqual(("needs_you", "Plan approval needed."), (result["state"], result["sentence"]))

    def test_a_supervised_stage_reads_as_working(self):
        result = summary.project(view("RUNNING", needs={"kind": "continue"}, headline="Builder working"))
        self.assertEqual(("working", "Builder working."), (result["state"], result["sentence"]))
        self.assertEqual("Working: Builder working.", summary.text(result))

    def test_waiting_on_another_run_is_not_a_request_to_the_person(self):
        result = summary.project(
            view("PAUSED_DEPENDENCY", needs={"kind": "dependency"}, headline="Waiting for another run")
        )
        self.assertEqual("working", result["state"])

    def test_done_only_when_completion_is_verified_against_the_current_checkout(self):
        complete = view("TASK_COMPLETE", done=True, next_command="autocode --follow-up TEXT")
        verified = summary.project(complete, completion_current=True)
        self.assertEqual(("done", None), (verified["state"], verified["next_command"]))
        for unverified in (False, None):
            with self.subTest(completion_current=unverified):
                result = summary.project(complete, completion_current=unverified)
                self.assertEqual("stopped", result["state"])
                self.assertIn("not shown as delivered", result["sentence"])

    def test_a_running_checkpoint_nothing_supervises_is_stopped_not_working(self):
        result = summary.project(
            view("RUNNING", needs={"kind": "continue"}, headline="Builder working"),
            stale=True,
            next_action="AutoResolver must reconcile retained attempt 1/terra-01 before any provider call",
        )
        self.assertEqual("stopped", result["state"])
        self.assertIn("nothing supervises its retained attempt", result["sentence"])
        self.assertIn("reconcile retained attempt 1/terra-01", result["sentence"])

    def test_any_other_stop_names_its_reason(self):
        result = summary.project(view("BLOCKED", stop_reason="The test command is missing"))
        self.assertEqual(("stopped", "The test command is missing."), (result["state"], result["sentence"]))
        unknown = summary.project(view(""))
        self.assertEqual(("stopped", "Stopped at an unknown state."), (unknown["state"], unknown["sentence"]))


if __name__ == "__main__":
    unittest.main()
