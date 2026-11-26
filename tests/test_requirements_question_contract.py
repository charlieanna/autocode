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

    def test_a_vague_request_gets_proposed_readings_and_a_sketch_not_a_blank_question(self):
        """Owner decision 2026-11-24: for "build me an adaptive engine" Requirements listed five
        readings in its own `why` and asked "what do you want this to do?" with no options, because
        this prompt forbade a menu. A person recognizes finished work when shown it, so a vague
        request now gets the readings as scenario options, a recommended one and an approach sketch;
        only an unreproduced bug still gets the open question asking for a recent example."""
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("propose, then ask (owner decision 2026-11-24)", text)
                self.assertIn("offer two to four concrete readings as options", text)
                self.assertIn("each written as a short user scenario a person can picture", text)
                self.assertIn(
                    "set proposed_default to the reading the request and the repository make most likely", text
                )
                self.assertIn("sketch in", text)
                self.assertIn("how the recommended reading would work", text)
                self.assertIn("A reported bug that cannot be reproduced", text)
                self.assertIn('with options=[] and proposed_default=""', text)
                self.assertNotIn("Do not invent a menu", text)
                self.assertNotIn("Ask one focused, open-ended question at a time", text)
        conversation = flat(prompts.get("fragments/autoplanner/requirements-conversation.md"))
        self.assertIn("propose before you ask", conversation)
        self.assertNotIn("do not turn that conversation into a choice menu", conversation)

    def test_a_recommended_reading_brings_its_forks_into_the_same_round(self):
        """2026-11-26 live runs: of four vague requests where Requirements recommended a reading, none
        asked that reading's forks in the same round ("When one reading is clearly the most likely"),
        so the person answered the reading, then waited a second round for its error text and edge
        cases; one plain run wrote those choices itself and marked them specified_in_source."""
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertNotIn("When one reading is clearly the most likely", text)
                self.assertIn("Whenever you set proposed_default, read the code that", text)
                self.assertIn("the forks it leaves", text)
                self.assertIn("so a person who takes your recommendation answers everything in one reply", text)
                self.assertIn("specified_in_source holds only when the cited source fixes what the user sees", text)
                self.assertIn("is a fork to ask, not a", text)

    def test_options_show_an_outcome_and_the_things_only_the_person_knows_come_in_the_same_round(self):
        """2026-11-24 live runs: options were prose labels ("Exactly 30x24 hours") with no example of
        which refund they refuse, and the Kibana service's language and log fields were asked a round
        later by the Planner, or never."""
        for conversational, text in self.rendered():
            with self.subTest(conversational=conversational):
                self.assertIn("Write each option as what the user would see on one concrete input", text)
                self.assertIn("so the user chooses between outcomes, not abstract labels", text)
                self.assertIn("the language or platform for new code in an empty repository", text)
                self.assertIn("the shape of external data the change reads or receives", text)
                self.assertIn("offer to take a pasted sample instead of an answer", text)
                self.assertIn("whether parts outside the repository", text)

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
                self.assertIn(
                    "while Tier 1 is still asking what the outcome is, outcome_scope is open_question naming "
                    "that question, each fork asked in the same round is named by its own dimension, and the "
                    "dimensions nothing yet decides are not_applicable",
                    text,
                )
                self.assertNotIn("for every dimension while Tier 1", text)
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
