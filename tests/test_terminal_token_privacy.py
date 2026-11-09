"""A terminal brief keeps issued Resolver bindings in the structured channel."""

import copy
import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_resolver_human as human
from goal_fixtures import body

from . import test_resolver_producers as fixtures


class TerminalTokenPrivacyTests(unittest.TestCase):
    setUp = fixtures.ResolverProducerTests.setUp

    def assert_private_render(self, expected_text):
        public = human.current(self.state)
        self.assertIsNotNone(public)
        before = copy.deepcopy(self.state)
        rendered = lifecycle.render(self.state, self.run)
        self.assertIn(expected_text, rendered)
        self.assertIn(public["request_id"], rendered)
        self.assertNotIn(public["request_token"], rendered)
        self.assertEqual(before, self.state)
        self.assertEqual(public, human.current(self.state), "Rendering must preserve the exact live binding")

    def test_v2_question_before_a_contract_hides_the_issued_token(self):
        self.state["settings"]["planning_flow"] = "v2"
        question = {
            "id": "Q1",
            "question": "Which output format?",
            "why": "Choose the interface",
            "options": ["Text", "JSON"],
            "proposed_default": "Text",
        }
        self.state["requirements_body"] = {"open_blocking_questions": [question]}
        human.queue(
            self.state,
            "clarification",
            {"stage": "requirements"},
            questions=[question],
        )
        self.assertEqual("escalate", human.evaluate(self.state))
        self.assert_private_render("Which output format?")

    def test_full_draft_question_hides_the_issued_token(self):
        lifecycle.install_draft(self.state, body(questions=True), origin="fixture")
        self.assertEqual("escalate", human.evaluate(self.state))
        self.assert_private_render("Answer ID:")

    def test_full_plan_approval_hides_the_resolver_token_and_keeps_the_plan_identity(self):
        lifecycle.install_draft(self.state, body(), origin="fixture")
        self.assertEqual("escalate", human.evaluate(self.state))
        self.assert_private_render("Approval token:")
