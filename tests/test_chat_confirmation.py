"""Explicit Requirements confirmations do not turn qualifications into consent."""

import unittest

import autocode_chat_confirmation as confirmation


class RequirementsConfirmationTests(unittest.TestCase):
    def test_clear_affirmations_allow_case_spacing_and_sentence_punctuation(self):
        for text in (
            "y",
            "yes",
            "correct",
            "yes correct",
            "  YES, correct!  ",
            "That's correct.",
            "Yes, that is correct.",
            "Yes, that’s correct!",
        ):
            with self.subTest(text=text):
                self.assertTrue(confirmation.affirmative(text))

    def test_corrections_negations_and_questions_are_not_confirmation(self):
        for text in (
            "",
            "no",
            "not correct",
            "yes, but future entries only",
            "yes correct except the result code",
            "correct the operation instead",
            "Is that correct?",
            "correct?",
            "yes; remove the tests",
            "yes\nchange the scope",
        ):
            with self.subTest(text=text):
                self.assertFalse(confirmation.affirmative(text))
