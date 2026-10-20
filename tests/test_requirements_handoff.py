"""Which Requirements reports the runner accepts as the handoff (autocode_requirements_handoff.accept)."""

import unittest

import autocode_requirements_handoff as handoff

QUESTION = {
    "id": "Q1",
    "question": "What would you like to build or change?",
    "why": "No software outcome has been named yet.",
    "options": [],
    "proposed_default": "",
    "kind": "decision",
    "category": "requested_outcome",
    "delegable": False,
}


def report(**change):
    value = {
        "summary": "The person asked my name.",
        "intended_outcome": "",
        "required_behaviors": [],
        "constraints": [],
        "acceptance_tests": [],
        "source_refs": [],
        "proposed_assumptions": [],
        "open_questions": [QUESTION],
        "requirements": [],
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
    }
    value.update(change)
    return value


class RequirementsHandoffTests(unittest.TestCase):
    def accept(self, value, settings=None, **saved):
        settings = {"spec_first": True} if settings is None else settings
        state = {"task": "What is your name?", "settings": settings, "stages": [], **saved}
        queued = []
        handoff.accept(
            state,
            value,
            {"output": None},
            check_code_refs=lambda *_: self.fail("An outcome question makes no code claim"),
            queue=lambda *args, **kwargs: queued.append(kwargs),
        )
        return state, queued

    def test_a_chat_reply_that_only_asks_for_the_outcome_needs_no_outcome_yet(self):
        state, queued = self.accept(report())
        self.assertEqual("", state["requirements_handoff"]["report"]["intended_outcome"])
        self.assertEqual([[QUESTION]], [row["questions"] for row in queued])

    def test_a_handoff_that_claims_work_still_needs_its_outcome(self):
        for value, settings in (
            (report(required_behaviors=["Greet"], acceptance_tests=["Prints hello"]), None),
            (report(), {}),
            (report(open_questions=[]), None),
        ):
            with self.subTest(value=value, settings=settings), self.assertRaisesRegex(ValueError, "needs an outcome"):
                self.accept(value, settings)

    def test_a_reply_that_names_no_goal_and_asks_nothing_waits_for_the_person(self):
        # Live, Kilo GLM 5.3 in idlecampus: "hi", then "Nothing yet, just saying hello." The honest
        # reply was refused, repaired twice and sent to the Investigator.
        reply = report(
            open_questions=[],
            assistant_reply="Understood. Whenever something comes to mind, just describe it.",
            summary="The user answered Q1 with 'Nothing yet, just saying hello.'",
        )
        state, queued = self.accept(reply, answers={"Q1": {"text": "Nothing yet, just saying hello."}})
        [[question]] = [row["questions"] for row in queued]
        self.assertEqual("requested_outcome", question["category"])
        self.assertEqual(handoff.OUTCOME_QUESTION, question["question"])
        self.assertTrue(question["id"].startswith("outcome-"))
        self.assertNotIn(question["id"], state["answers"], "a new id: an answered question cannot be asked again")
        self.assertEqual("", state["requirements_handoff"]["report"]["intended_outcome"])

    def test_without_spec_first_or_with_claimed_work_a_goalless_reply_is_still_refused(self):
        chat = {"open_questions": [], "assistant_reply": "Understood."}
        for value, settings in ((report(**chat), {}), (report(**chat, required_behaviors=["Greet"]), None)):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "needs an outcome"):
                self.accept(value, settings)


if __name__ == "__main__":
    unittest.main()
