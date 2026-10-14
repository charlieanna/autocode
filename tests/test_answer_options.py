"""An answer to a question that offers options must name one of them (#1013)."""

import json
import unittest

import autocode_answer_options as answer_options

from . import test_subprocess

QUESTION = {"id": "Q1", "options": ["CLI", "Web"], "proposed_default": "CLI, with no server"}


class AnswerOptionTests(unittest.TestCase):
    def test_an_offered_label_or_the_proposed_default_is_accepted_and_stored_as_offered(self):
        for text, stored in (("CLI", "CLI"), (" Web ", "Web"), ("CLI, with no server", "CLI, with no server")):
            with self.subTest(text=text):
                self.assertIsNone(answer_options.refusal(QUESTION, text))
                self.assertEqual(stored, answer_options.offered(QUESTION, text))

    def test_text_that_names_no_option_is_refused_with_the_valid_labels(self):
        for text in ("cli", "CLI please", "Command line"):
            with self.subTest(text=text), self.assertRaises(ValueError) as refused:
                answer_options.offered(QUESTION, text)
            self.assertIn("'CLI'; 'Web'; 'CLI, with no server'", str(refused.exception))
            self.assertIn("--feedback TEXT", str(refused.exception))
        self.assertIn("/feedback TEXT", answer_options.refusal(QUESTION, "cli", feedback="/feedback TEXT"))

    def test_a_question_without_options_takes_free_text(self):
        for question in ({"id": "Q1", "options": [], "proposed_default": "CLI"}, {"id": "Q1"}):
            with self.subTest(question=question):
                self.assertEqual([], answer_options.labels(question))
                self.assertEqual("Anything at all", answer_options.offered(question, "Anything at all"))


class AnswerOptionsCLITests(unittest.TestCase):
    """The fake planner asks Q1 with options ["CLI", "Web"] and a proposed default."""

    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = test_subprocess.SubprocessFlow.new_run_engine_args

    def waiting(self):
        self.launch(["Build a greeting tool", "--no-chat"], 2)
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        question = next(row for row in state["pending_questions"] if row["id"] == "Q1")
        self.assertEqual(["CLI", "Web"], question["options"])
        return run, state, question

    def test_each_offered_label_is_saved_as_the_answer(self):
        _, _, question = self.waiting()
        for label in [*question["options"], question["proposed_default"]]:
            with self.subTest(label=label):
                self.setUp()
                run, _, _ = self.waiting()
                self.launch(["--run-dir", str(run), "--answer", f"Q1={label}"], 0)
                answered = self.saved()[1]
                self.assertEqual(label, answered["answers"]["Q1"]["text"])
                self.assertEqual([], answered["pending_questions"])

    def test_an_answer_naming_no_option_changes_nothing_and_keeps_the_token(self):
        run, state, _ = self.waiting()
        before = (run / "state.json").read_bytes()
        token = state["resolver_human_request"]["request_token"]
        result = self.launch(["--run-dir", str(run), "--answer", "Q1=Command line, please"], 2)
        self.assertIn("Q1 offers options; answer with one of these labels exactly: 'CLI'; 'Web'", result.stderr)
        self.assertEqual(before, (run / "state.json").read_bytes())
        # The refused answer consumed nothing: the same token still answers with a label.
        self.launch(["--run-dir", str(run), "--answer", "Q1=Web", "--resolver-token", token], 0)
        self.assertEqual("Web", json.loads((run / "state.json").read_text())["answers"]["Q1"]["text"])

    def test_chat_asks_again_after_an_answer_naming_no_option(self):
        # Input ends at the plan approval prompt, which leaves the run waiting there.
        result = self.launch(["Build a greeting tool", "--chat"], 2, answers="Command line\nCLI\n")
        self.assertIn("Q1 offers options; answer with one of these labels exactly", result.stdout)
        self.assertIn("/feedback TEXT", result.stdout)
        state = self.saved()[1]
        self.assertEqual("CLI", state["answers"]["Q1"]["text"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])


if __name__ == "__main__":
    unittest.main()
