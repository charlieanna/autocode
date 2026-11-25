"""Requirements shows how it would do the task, and the person confirms it before any plan (#4171).

The oracles are the public review question, the questions a handoff stops on and the saved
confirmation the Planner receives, driven through ``autocode_requirements_handoff.accept`` and
``autocode_requirements_review.after_answers`` as a run calls them.
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

import autocode_approach_sketch as approach_sketch
import autocode_requirements_handoff as handoff
import autocode_requirements_review as review

from tests import test_requirements_review

TASK = "Send a reminder email before each booking."
SKETCH = {
    "summary": "A scheduled job reads tomorrow's bookings each hour and sends one email per booking.",
    "pieces": ["Hourly job", "Reminder email template", "Sent-reminder record so nothing is sent twice"],
    "band": "small: one new module and one template",
    "alternatives": [],
    "out_of_scope": ["Text-message reminders"],
}
ALTERNATIVE = {"label": "On booking save", "tradeoff": "No job to run, but bookings made late get no reminder."}


def handoff_report(approach=SKETCH, **extra):
    return {
        "job": "build",
        "summary": "Booking reminders.",
        "intended_outcome": TASK,
        "requirements": [{"id": "R1", "text": TASK, "source_quote": TASK}],
        "required_behaviors": ["A booking tomorrow gets one reminder email"],
        "constraints": [],
        "acceptance_tests": ["A booking for tomorrow gets exactly one reminder"],
        "source_refs": ["task"],
        "open_questions": [],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
        "proposed_assumptions": [],
        "approach": approach,
        **extra,
    }


class ApproachSketchTests(unittest.TestCase):
    def accept(self, report, *, settings=None, answers=None):
        state = {
            "task": TASK,
            "settings": settings or {},
            "stages": [],
            "workflow": {"kind": None, "then": "draft_plan"},
            "answers": dict(answers or {}),
        }
        queued: list[dict] = []
        with patch.dict(
            sys.modules, {"autocode_assumption_ledger": test_requirements_review.SettingTests._ledger_stub()}
        ):
            handoff.accept(
                state,
                report,
                {"output": "iter/001/req-01.json"},
                check_code_refs=lambda *_: None,
                queue=lambda *args, **kwargs: queued.append(kwargs),
            )
        return state, queued

    def answer(self, state, text):
        question = review.review_question(state)
        state["answers"] = {**state.get("answers", {}), question["id"]: {"text": text}}
        with patch.dict(
            sys.modules, {"autocode_assumption_ledger": test_requirements_review.SettingTests._ledger_stub()}
        ):
            review.after_answers(state)
        return state

    def test_a_clear_request_with_one_way_to_do_it_goes_straight_to_the_planner(self):
        state, queued = self.accept(handoff_report())
        self.assertEqual([], queued)
        self.assertEqual([], review.questions(state))
        self.assertEqual(review.NEXT, state["next_stage"])
        # The Planner still receives the sketch with the handoff, unconfirmed.
        self.assertEqual(SKETCH["summary"], state["requirements_handoff"]["report"]["approach"]["summary"])
        self.assertNotIn(approach_sketch.CONFIRMED, state["requirements_handoff"])

    def test_alternatives_stop_with_the_understanding_and_the_approach_in_one_confirmation(self):
        state, _ = self.accept(handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]}))
        question = review.review_question(state)
        self.assertEqual([question], review.questions(state))
        text = question["question"]
        self.assertIn("How I would do it: " + SKETCH["summary"], text)
        self.assertIn("  - Sent-reminder record so nothing is sent twice", text)
        self.assertIn("Size: small: one new module and one template", text)
        self.assertIn("Not part of this task:\n  - Text-message reminders", text)
        self.assertIn("  - On booking save: No job to run, but bookings made late get no reminder.", text)
        self.assertIn("Replying plan takes the approach above.", text)
        self.assertIn("how it would do it", question["why"])

    def test_a_handoff_written_after_the_persons_answers_asks_before_planning(self):
        answers = {"Q1": {"text": "Only for paid bookings"}}
        paid = {"id": "R2", "text": "Only paid bookings get reminders", "source_quote": "Only for paid bookings"}
        report = handoff_report()
        state, _ = self.accept({**report, "requirements": [*report["requirements"], paid]}, answers=answers)
        self.assertEqual(["Q1"], state["requirements_handoff"]["answers_seen"])
        question = review.review_question(state)
        self.assertEqual([question], review.questions(state))
        self.assertIn("How I would do it:", question["question"])
        self.assertNotIn("Other ways to do it", question["question"])

    def test_plan_confirms_the_sketched_approach_for_the_planner(self):
        state, _ = self.accept(handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]}))
        self.answer(state, "plan")
        confirmed = state["requirements_handoff"][approach_sketch.CONFIRMED]
        self.assertEqual("sketch", confirmed["choice"])
        self.assertEqual(SKETCH["summary"], confirmed["approach"])
        self.assertEqual(review.CONFIRMED_BY_REVIEW, confirmed["source"])
        self.assertEqual(review.NEXT, state["next_stage"])
        self.assertEqual([], review.questions(state))

    def test_naming_an_alternative_confirms_it_without_running_requirements_again(self):
        state, _ = self.accept(handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]}))
        self.answer(state, "  on booking SAVE. ")
        confirmed = state["requirements_handoff"][approach_sketch.CONFIRMED]
        self.assertEqual("On booking save", confirmed["choice"])
        self.assertEqual("On booking save: " + ALTERNATIVE["tradeoff"], confirmed["approach"])
        self.assertEqual(review.NEXT, state["next_stage"])
        self.assertTrue(review.approves(state, {"text": "On booking save"}))

    def test_a_correction_sends_requirements_back_and_confirms_nothing(self):
        state, _ = self.accept(handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]}))
        self.answer(state, "Send it two days before instead")
        self.assertNotIn(approach_sketch.CONFIRMED, state["requirements_handoff"])
        self.assertEqual(review.STAGE, state["next_stage"])

    def test_skip_requirements_never_stops_and_records_who_confirmed(self):
        state, queued = self.accept(
            handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]}), settings={"skip_requirements": True}
        )
        self.assertEqual([], queued)
        self.assertEqual([], review.questions(state))
        confirmed = state["requirements_handoff"][approach_sketch.CONFIRMED]
        self.assertEqual((review.CONFIRMED_BY_SKIP, "sketch"), (confirmed["source"], confirmed["choice"]))

    def test_a_confirmation_binds_to_the_exact_sketch(self):
        report = handoff_report({**SKETCH, "alternatives": [ALTERNATIVE]})
        saved: dict = {}
        approach_sketch.confirm(saved, report, choice=None, source="requirements_review")
        self.assertIsNotNone(approach_sketch.confirmation(saved, report))
        self.assertFalse(approach_sketch.asks({}, saved, report, skipped=False))
        changed = handoff_report(
            {**SKETCH, "summary": "Send the email when the booking is made.", "alternatives": [ALTERNATIVE]}
        )
        self.assertIsNone(approach_sketch.confirmation(saved, changed))
        self.assertTrue(approach_sketch.asks({}, saved, changed, skipped=False))

    def test_a_report_without_a_sketch_shows_and_asks_nothing_new(self):
        for approach in (None, {}, {"summary": "  "}, "a string"):
            with self.subTest(approach=approach):
                report = handoff_report(approach)
                self.assertEqual([], approach_sketch.lines(report, review.APPROVE))
                self.assertFalse(approach_sketch.asks({}, {"answers_seen": ["Q1"]}, report, skipped=False))
                self.assertIsNone(approach_sketch.chosen(report, "plan"))


if __name__ == "__main__":
    unittest.main()
