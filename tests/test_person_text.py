"""A person-only Resolver question in the status view carries no CLI machinery (#3145).

A live run (20261111-210549-vscode-10d2c3) paused on an AutoResolver request whose question, rendered
verbatim by the VS Code chat, told the person to type `--resolver-request ID --resolver-token -
(reads AUTOCODE_RESOLVER_TOKEN) --resolver-response provide_information --resolver-message TEXT, then
autocode resume`. The view names the reply as an argument array; the question shows the person's
sentences only, while the saved request and the stop reason keep the operator advice.
"""

import unittest

import autocode_person_text as person_text
import autocode_recovery_limits as recovery_limits
import autocode_resolver_human as human
import autocode_run_view as run_view

from . import test_blocker_next_action, test_resolver_human

PERSON = (
    "AutoResolver could not resolve the Builder's model limit. Provide corrective information or leave the run paused."
)
ATTEMPT = "001/builder-01"


def composed():
    """The request text resolver_runtime composes when no recovery grant is eligible."""
    return PERSON + " " + recovery_limits.advice(allow_grant=False, attempt=ATTEMPT)


class PlainTextTests(unittest.TestCase):
    def test_operator_sentences_are_dropped_and_person_sentences_kept(self):
        shown = person_text.plain(composed())
        self.assertTrue(shown.startswith(PERSON), shown)
        self.assertFalse(person_text.machinery(shown), shown)
        self.assertIn("Resuming without setting the uncertain attempt aside will hold", shown)

    def test_each_kind_of_machinery_is_recognized(self):
        for text in (
            "Run it with --resume-paused.",
            "The token is read from AUTOCODE_RESOLVER_TOKEN.",
            "Then autocode resume.",
            "Use autocode --run-dir RUN.",
        ):
            with self.subTest(text=text):
                self.assertTrue(person_text.machinery(text))
                self.assertEqual(person_text.FALLBACK, person_text.plain(text))
        for text in ("A hand-written -- aside.", "Use openai/gpt-6-sol at medium.", "The run is self-paced."):
            with self.subTest(text=text):
                self.assertFalse(person_text.machinery(text))
                self.assertEqual(text, person_text.plain(text))

    def test_lines_are_kept_and_a_machinery_line_is_dropped(self):
        text = (
            "Approve the correction?\nAC1 current: \"a\"\nRun --edit-goal body.json first.\n\nReply exactly 'accept'."
        )
        self.assertEqual(
            "Approve the correction?\nAC1 current: \"a\"\n\nReply exactly 'accept'.", person_text.plain(text)
        )

    def test_empty_text_passes_through(self):
        for text in (None, ""):
            self.assertEqual(text, person_text.plain(text))

    def test_an_option_loses_its_command_clause(self):
        cases = {
            "Abandon the uncertain attempt with --abandon-stage 001/builder-01": "Abandon the uncertain attempt",
            "Acknowledge the pause with autocode resume --no-progress-limit N": "Acknowledge the pause",
            "Allow more plan reviews with --planning-review-call-limit N": "Allow more plan reviews",
            "Leave paused": "Leave paused",
            "openai/gpt-6-sol": "openai/gpt-6-sol",
            "--resume-paused": None,
        }
        for option, shown in cases.items():
            with self.subTest(option=option):
                self.assertEqual(shown, person_text.plain_option(option))

    def test_a_question_row_keeps_its_other_fields(self):
        row = {"id": "R1", "question": composed(), "why": "Kept.", "options": ["--resume-paused", "Leave paused"]}
        shown = person_text.question(row)
        self.assertEqual(("R1", "Kept.", ["Leave paused"]), (shown["id"], shown["why"], shown["options"]))
        self.assertFalse(person_text.machinery(shown["question"]))
        self.assertEqual(composed(), row["question"])


class PersonOnlyQuestionTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    request = test_resolver_human.ResolverHumanTests.request
    publish_operational = test_resolver_human.ResolverHumanTests.publish_operational

    def queue_blocker(self):
        request = test_blocker_next_action.blocker_request()
        request["decision_needed"] = composed()
        request["options"] = [
            "Provide corrective information",
            "Leave paused",
            f"Abandon the uncertain attempt with --abandon-stage {ATTEMPT}",
        ]
        human.queue(
            self.state,
            "blocker",
            {"stage": "resolve"},
            request=request,
            evidence={"diagnosis": "Model limit", "output": "iterations/001/resolver-01.json"},
        )
        self.state["stages"].append(
            {"stage": "resolve", "output": "iterations/001/resolver-01.json", "exit_code": 0, "changed_files": []}
        )

    def view(self):
        return run_view.view(self.state, run_dir=str(self.root / "run"), workspace=str(self.root))

    def assert_plain(self, need):
        question = need["questions"][0]
        for text in (question["question"], question["why"], *question["options"]):
            self.assertFalse(person_text.machinery(text), text)

    def test_a_published_blocker_question_and_its_chat_message_name_no_flags(self):
        self.state["status"] = "PAUSED_RESOLVER"
        self.queue_blocker()
        self.assertEqual("escalate", human.evaluate(self.state))
        view = self.view()
        need = view["needs"]
        self.assertEqual(("answer", "blocker"), (need["kind"], need["resolver_scope"]))
        self.assert_plain(need)
        self.assertTrue(need["questions"][0]["question"].startswith(PERSON))
        self.assertEqual(
            ["Provide corrective information", "Leave paused", "Abandon the uncertain attempt"],
            need["questions"][0]["options"],
        )
        message = next(row for row in view["chat"]["messages"] if row["id"].startswith("resolver-"))
        self.assertTrue(message["text"].startswith(PERSON), message["text"])
        self.assertFalse(person_text.machinery(message["text"]), message["text"])
        # The reply stays the named argument array, and the saved request keeps the operator advice.
        self.assertEqual("resolver_response", view["chat"]["input"]["kind"])
        self.assertIn("--resolver-response", view["chat"]["input"]["next_command"])
        self.assertEqual(composed(), human.current(self.state)["request"]["decision_needed"])

    def test_a_pending_blocker_proposal_question_names_no_flags(self):
        # The issue's run: RESOLVER_PENDING with an unpublished proposal the writer has not settled.
        self.queue_blocker()
        self.state["status"] = "RESOLVER_PENDING"
        need = self.view()["needs"]
        self.assertEqual(("answer", "blocker"), (need["kind"], need["resolver_scope"]))
        self.assert_plain(need)
        self.assertTrue(need["questions"][0]["question"].startswith(PERSON))

    def test_an_operational_exhaustion_question_names_no_flags(self):
        self.contract()
        self.publish_operational()
        published = human.current(self.state)
        self.state["pending_questions"][0]["question"] = composed()
        need = self.view()["needs"]
        self.assertEqual(("answer", "operational_exhaustion"), (need["kind"], need["resolver_scope"]))
        self.assertEqual(published["request_id"], need["resolver_request_id"])
        self.assert_plain(need)

    def test_a_requirements_question_is_shown_as_asked(self):
        text = "Should the CLI accept --verbose?"
        self.state["status"] = "AWAITING_USER"
        self.state["pending_questions"] = [{"id": "Q1", "question": text, "why": "", "options": []}]
        need = self.view()["needs"]
        self.assertNotIn("resolver_scope", need)
        self.assertEqual(text, need["questions"][0]["question"])


if __name__ == "__main__":
    unittest.main()
