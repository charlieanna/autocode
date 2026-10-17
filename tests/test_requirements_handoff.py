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
    def accept(self, value, settings=None):
        settings = {"spec_first": True} if settings is None else settings
        state = {"task": "What is your name?", "settings": settings, "stages": []}
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


if __name__ == "__main__":
    unittest.main()
