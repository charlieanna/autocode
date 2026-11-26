"""What Requirements understood is shown beside its questions, not only in a chat client.

A live run on "make the todo app better" (2026-11-26) wrote its restatement and the code it found
in the report's summary, then asked which reading the person meant; the CLI and the status view
showed the question alone. The status view now names that text once (``requirements.understood``)
and the CLI and the channel bridge show it above Requirements' own questions."""

import unittest

import autocode_bridge_relay as relay
import autocode_goal_lifecycle as lifecycle
import autocode_requirements_review as requirements_review
import autocode_run_view as run_view

QUESTION = {
    "id": "Q1",
    "question": "What kind of better do you mean?",
    "why": "The request names no outcome.",
    "options": ["Robustness: done 99 prints an error", "New commands: delete and edit"],
    "proposed_default": "",
    "kind": "decision",
    "category": "requested_outcome",
    "delegable": False,
    "convention_ref": "",
}
SUMMARY = "You want the todo app improved. I read todo.py: done crashes on a bad number."


def asking(report_extra=None):
    """A run stopped on Requirements' published question, its report saying ``SUMMARY``."""
    report = {
        "summary": SUMMARY,
        "open_questions": [QUESTION],
        "required_behaviors": [],
        "acceptance_tests": [],
        **(report_extra or {}),
    }
    state = {
        "version": 3,
        "task_id": "task-1",
        "task": "make the todo app better",
        "status": "RUNNING",
        "next_stage": "draft_plan",
        "settings": {"joint_planning": True},
        "answers": {},
        "user_events": [],
        "requirements_handoff": {"report": report, "output": "req.json"},
    }
    requirements_review.wait(state, {"output": "req.json"}, lifecycle.human.queue)
    lifecycle.human.evaluate(state)
    return state


class UnderstoodTests(unittest.TestCase):
    def test_the_summary_is_what_a_run_without_a_chat_understood(self):
        self.assertEqual(SUMMARY, requirements_review.understood(asking()))

    def test_a_chat_reply_wins_and_loses_its_classification_sentence(self):
        state = asking({"assistant_reply": "This is a vague build request. Which kind of better do you mean?"})
        self.assertEqual("Which kind of better do you mean?", requirements_review.understood(state))

    def test_nothing_said_is_empty(self):
        self.assertEqual("", requirements_review.understood(asking({"summary": "  "})))
        self.assertEqual("", requirements_review.understood({}))

    def test_the_status_view_names_it_once(self):
        self.assertEqual(SUMMARY, run_view.requirements_view(asking())["understood"])

    def test_the_cli_shows_it_above_the_questions(self):
        screen = lifecycle.render(asking())
        self.assertIn(f"Requirements: {SUMMARY}\nAnswer ID: Q1 - What kind of better do you mean?", screen)

    def test_the_cli_confirmation_does_not_repeat_it(self):
        self.assertEqual([], lifecycle._understood(asking(), [{"id": requirements_review.PREFIX + "abc"}]))
        self.assertEqual([], lifecycle._understood(asking(), []))

    def test_the_bridge_shows_it_above_the_questions_but_not_above_a_confirmation(self):
        view = {"needs": {"kind": "answer", "questions": [QUESTION]}, "requirements": {"understood": SUMMARY}}
        text = relay.questions_markdown(view["needs"], view)
        self.assertLess(text.index(SUMMARY), text.index("[Q1]"))
        review = {"id": requirements_review.PREFIX + "abc", "question": "Here is what I understand."}
        self.assertNotIn(SUMMARY, relay.questions_markdown({"questions": [review]}, view))


if __name__ == "__main__":
    unittest.main()
