"""Requirements asks in two tiers, and never parks a user-visible fork in an assumption (board #11 P1.2)."""

import unittest

import autocode_prompts as prompts
import autocode_requirements_prompt as requirements_prompt


def flat(text):
    return " ".join(text.split())


class RequirementsQuestionContractTests(unittest.TestCase):
    def rendered(self):
        for schema in ({"properties": {"assistant_reply": {}}}, {"properties": {}}):
            yield (
                bool(schema["properties"]),
                flat(requirements_prompt.render(prompts.get("requirements-gather.md"), schema)),
            )

    def test_open_ended_questions_are_only_for_missing_intent_or_an_unreproduced_bug(self):
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("TIER 1, MISSING INTENT. Ask one focused, open-ended question at a time", text)
                self.assertIn("no outcome is named yet, or a reported bug cannot be reproduced", text)
                self.assertIn('Use options=[] and proposed_default="" for these exploratory questions', text)

    def test_code_grounded_forks_are_asked_together_with_options_and_a_recommendation(self):
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("read the code the change touches (the target module, its callers and its tests)", text)
                self.assertIn(
                    "Ask every fork you found in this one round (at most five questions), never one per round", text
                )
                self.assertIn("says in why which fork it is", text)
                self.assertIn(
                    "offers two to four concrete options, and sets proposed_default to the option you recommend", text
                )
                self.assertIn("up to five questions", text)
                self.assertNotIn("up to three", text)

    def test_forks_are_never_parked_in_assumptions_and_confirmation_is_never_asked(self):
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("Never park a fork in proposed_assumptions", text)
                self.assertNotIn("make it a proposed assumption or an open question", text)
                self.assertIn('Never ask a generic confirmation ("Is this right?"', text)
                self.assertIn("zero questions is the right answer for a clear request", text)
                self.assertIn("tests, test commands, regression proofs", text)
        policy = flat(prompts.get("fragments/autoplanner/assumption-policy.md"))
        self.assertIn("User-visible behavior (inputs and edge cases", policy)
        self.assertIn("is never an assumption", policy)
        self.assertIn('category="technical", kind="inferable"', policy)
        conversation = flat(prompts.get("fragments/autoplanner/requirements-conversation.md"))
        self.assertIn("ask the concrete forks you found together, each with its options", conversation)

    def test_the_audit_and_every_citation_are_explained_as_checked_against_what_was_read(self):
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("AMBIGUITY AUDIT. In ambiguity_audit", text)
                for dimension in (
                    "outcome_scope",
                    "inputs_and_edge_cases",
                    "error_and_failure_behavior",
                    "compatibility_and_callers",
                    "ux_and_output_contract",
                    "state_and_side_effects",
                ):
                    self.assertIn(dimension, text)
                self.assertIn("the runner checks your tool calls", text)
        self.assertIn(
            "refuses a citation of a file it never read", flat(prompts.get("fragments/autoplanner/source-refs-rule.md"))
        )
        question_policy = flat(prompts.get("fragments/autoplanner/question-policy.md"))
        self.assertIn("AutoCode settles the question itself only when that citation checks out", question_policy)
        provenance = flat(prompts.get("fragments/goals/decision-provenance.md"))
        self.assertIn("An agent_proposed row may only be category=technical", provenance)


if __name__ == "__main__":
    unittest.main()
