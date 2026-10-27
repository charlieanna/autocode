"""A refreshed handoff keeps authenticated requirements and reports real changes precisely."""

import copy
import unittest

import autocode_goals as goals

TASK = (
    "Fix checkout showing '100.0 USD' for a 100 USD subtotal when the expected final total is '$110.00'. "
    "Preserve existing behavior for a zero total and malformed receipts."
)
PRESERVE = "Preserve existing behavior for a zero total and malformed receipts."
TEXT = "Preserve existing behavior for a zero total and for malformed receipts."


class RequirementRetentionTests(unittest.TestCase):
    def state(self):
        return {
            "task": TASK,
            "requirements_handoff": {"report": {"requirements": [{"id": "R3", "text": TEXT, "source_quote": TASK}]}},
        }

    def report(self, *, text=TEXT, quote=PRESERVE):
        return {"requirements": [{"id": "R3", "text": text, "source_quote": quote}], "ignored_statements": []}

    def test_saved_incident_narrower_quote_keeps_the_original_provenance(self):
        state = self.state()
        before = copy.deepcopy(state)
        report = self.report()
        goals.check_requirement_handoff(state, report)
        self.assertEqual(TASK, report["requirements"][0]["source_quote"])
        self.assertEqual(TEXT, report["requirements"][0]["text"])
        self.assertEqual(before, state)

    def test_narrow_quote_with_changed_requirement_is_not_restored(self):
        with self.assertRaisesRegex(ValueError, "changed source_quote for requirement R3"):
            goals.check_requirement_handoff(self.state(), self.report(text="Malformed receipts may return zero."))

    def test_unsupported_quote_is_not_replaced_by_old_provenance(self):
        with self.assertRaisesRegex(ValueError, "source_quote is not in"):
            goals.check_requirement_handoff(self.state(), self.report(quote="Receipts may be discarded."))

    def test_quote_restoration_does_not_mask_a_forged_replacement_basis(self):
        report = self.report()
        report["ignored_requirements"] = [
            {
                "requirement_id": "R3",
                "reason": "The user changed it.",
                "basis": "user_feedback",
                "event_id": "invented-event",
            }
        ]
        with self.assertRaisesRegex(ValueError, "needs a saved user answer or feedback event"):
            goals.check_requirement_handoff(self.state(), report)

    def test_an_old_quote_must_still_be_grounded_in_the_current_sources(self):
        state = self.state()
        state["requirements_handoff"]["report"]["requirements"][0]["source_quote"] = "Unbacked text. " + TASK
        with self.assertRaisesRegex(ValueError, "changed source_quote for requirement R3"):
            goals.check_requirement_handoff(state, self.report())

    def test_malformed_omission_inventory_remains_a_validation_error(self):
        for ignored in (None, 1, "R3", {}):
            report = self.report()
            report["ignored_requirements"] = ignored
            with (
                self.subTest(ignored=ignored),
                self.assertRaisesRegex(ValueError, "ignored_requirements must be an array"),
            ):
                goals.check_requirement_handoff(self.state(), report)

    def test_every_real_quote_change_and_omission_is_named_in_one_rejection(self):
        state = self.state()
        state["task"] += " No commits."
        first = TASK.removesuffix(" " + PRESERVE)
        previous = state["requirements_handoff"]["report"]["requirements"]
        previous += [
            {"id": "R4", "text": "The combined total is $110.00.", "source_quote": first},
            {"id": "R5", "text": "No commits.", "source_quote": "No commits."},
        ]
        feedback = "The real combined checkout_total for a 100 subtotal must produce $110.00."
        state["brief_feedback"] = [{"id": "feedback-1", "text": feedback}]
        state["user_events"] = copy.deepcopy(state["brief_feedback"])
        report = self.report()
        report["requirements"].append({"id": "R4", "text": "The combined total is $110.00.", "source_quote": feedback})
        with self.assertRaises(ValueError) as caught:
            goals.check_requirement_handoff(state, report)
        message = str(caught.exception)
        self.assertIn("changed source_quote for requirement R4", message)
        self.assertIn("dropped requirement R5", message)
        self.assertIn(first, message)
        self.assertIn(feedback, message)
        self.assertNotIn("dropped requirement R3", message)

    def test_real_omission_still_requires_a_saved_user_event(self):
        state = self.state()
        report = {"requirements": [], "ignored_statements": []}
        with self.assertRaisesRegex(ValueError, "dropped requirement R3"):
            goals.check_requirement_handoff(state, report)
        report["ignored_requirements"] = [
            {
                "requirement_id": "R3",
                "reason": "The user removed it.",
                "basis": "user_feedback",
                "event_id": "feedback-1",
            }
        ]
        with self.assertRaisesRegex(ValueError, "needs a saved user answer or feedback event"):
            goals.check_requirement_handoff(state, report)
        state["brief_feedback"] = [{"id": "feedback-1", "text": "Remove the preservation requirement."}]
        state["user_events"] = copy.deepcopy(state["brief_feedback"])
        goals.check_requirement_handoff(state, report)
