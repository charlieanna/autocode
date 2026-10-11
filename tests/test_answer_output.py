"""Human-readable answer hints use only the public frontier."""

import copy
import unittest

import autocode_answer_output as output


class AnswerOutputTests(unittest.TestCase):
    def test_pending_ids_questions_and_exact_current_token_are_shown(self):
        view = {
            "needs": {
                "kind": "answer",
                "questions": [
                    {"id": "audience", "question": "Who will read it?"},
                    {"id": "punctuation", "question": "Use an exclamation mark?"},
                ],
                "resolver_token": "current-token",
            }
        }
        before = copy.deepcopy(view)
        self.assertEqual(
            "Pending questions:\n  audience: Who will read it?\n"
            "  punctuation: Use an exclamation mark?\nResolver token: current-token",
            output.summary(view),
        )
        self.assertEqual(before, view)

    def test_only_the_current_public_token_is_used(self):
        view = {
            "resolver_token": "old-token",
            "needs": {
                "kind": "answer",
                "questions": [{"id": "remaining", "question": "Choose?"}],
                "resolver_token": "rotated-token",
            },
        }
        self.assertEqual(
            "Pending questions:\n  remaining: Choose?\nResolver token: rotated-token",
            output.summary(view, after_answer=True),
        )

    def test_missing_public_token_is_not_inferred(self):
        self.assertEqual(
            "Pending questions:\n  q: Choose?",
            output.summary(
                {
                    "needs": {
                        "kind": "answer",
                        "questions": [{"id": "q", "question": "Choose?"}],
                    }
                }
            ),
        )

    def test_operational_question_keeps_its_response_action(self):
        view = {
            "needs": {
                "kind": "answer",
                "questions": [{"id": "help", "question": "Provide information?"}],
                "resolver_token": "request-token",
                "action": "--resolver-response provide_information",
            }
        }
        self.assertIn("Response action: --resolver-response provide_information", output.summary(view))

    def test_last_answer_names_the_empty_answer_frontier(self):
        for need in (None, {"kind": "continue"}, {"kind": "approve_plan", "token": "approval-token"}):
            with self.subTest(need=need):
                self.assertEqual("No questions left to answer.", output.summary({"needs": need}, after_answer=True))
                self.assertIsNone(output.summary({"needs": need}))

    def test_multiline_question_is_readable_on_one_line(self):
        self.assertEqual(
            "Pending questions:\n  q: First line second line",
            output.summary(
                {
                    "needs": {
                        "kind": "answer",
                        "questions": [{"id": "q", "question": "First line\nsecond line"}],
                    }
                }
            ),
        )
