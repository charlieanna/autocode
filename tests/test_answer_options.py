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

    def test_conversational_forms_of_a_label_name_it_and_store_the_offered_label(self):
        for text, stored in (("cli", "CLI"), ("`CLI`", "CLI"), ("'Web'", "Web"), ("web", "Web")):
            with self.subTest(text=text):
                self.assertIsNone(answer_options.refusal(QUESTION, text))
                self.assertEqual(stored, answer_options.offered(QUESTION, text))

    def test_a_complete_label_matches_without_markdown_or_case_differences(self):
        question = {
            "id": "Q1",
            "options": [
                "The full-screen terminal chat (today's `autocode chat`)",
                "The Figma design workflow (today's `autocode ui`)",
            ],
        }
        for text in (
            "The full-screen terminal chat (today's autocode chat)",
            "The Figma design workflow (today’s autocode ui)",
            "the FULL-SCREEN terminal chat (today's `autocode chat`)",
        ):
            with self.subTest(text=text):
                self.assertIsNone(answer_options.refusal(question, text))
                self.assertEqual(
                    "The Figma design workflow (today's `autocode ui`)"
                    if "figma" in text.casefold()
                    else "The full-screen terminal chat (today's `autocode chat`)",
                    answer_options.offered(question, text),
                )

    def test_an_answer_containing_several_labels_names_none_of_them(self):
        question = {"id": "Q1", "options": ["The full-screen terminal chat", "The Figma design workflow"]}
        self.assertIsNotNone(
            answer_options.refusal(question, "the full-screen terminal chat or the figma design workflow")
        )
        with self.assertRaises(ValueError):
            answer_options.offered(question, "the full-screen terminal chat or the figma design workflow")

    def test_mentions_negations_and_extensions_are_not_choices(self):
        question = {"id": "Q1", "options": ["JSON", "XML"]}
        for text in ("I do not want JSON", "Can you explain JSON before I decide?", "JSONL", "JSON, but only later"):
            with self.subTest(text=text):
                self.assertIsNotNone(answer_options.refusal(question, text))
                with self.assertRaises(ValueError):
                    answer_options.offered(question, text)

    def test_prose_about_a_long_label_requires_interpretation_not_substring_matching(self):
        question = {"id": "Q1", "options": ["The full-screen terminal chat", "The Figma design workflow"]}
        for text in (
            "I do not want the full-screen terminal chat",
            "I'd like the Figma design workflow, please",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                answer_options.offered(question, text)

    def test_normalization_does_not_choose_between_distinct_case_sensitive_labels(self):
        question = {"id": "Q1", "options": ["JSON", "json"]}
        self.assertEqual("json", answer_options.offered(question, "json"))
        with self.assertRaises(ValueError):
            answer_options.offered(question, "Json")

    def test_text_that_names_no_option_is_refused_with_the_valid_labels(self):
        for text in ("CLI please", "Command line"):
            with self.subTest(text=text), self.assertRaises(ValueError) as refused:
                answer_options.offered(QUESTION, text)
            self.assertIn("'CLI'; 'Web'; 'CLI, with no server'", str(refused.exception))
            self.assertIn("--feedback TEXT", str(refused.exception))
        self.assertIn("/feedback TEXT", answer_options.refusal(QUESTION, "Command line", feedback="/feedback TEXT"))

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

    def test_negating_the_default_keeps_the_public_question_and_cost_unchanged(self):
        run, _, question = self.waiting()
        self.assertGreaterEqual(len(question["proposed_default"]), 4)
        args = ["--run-dir", str(run), "--status", "--json"]
        before = json.loads(self.launch(args, 0).stdout)["view"]
        self.launch(["--run-dir", str(run), "--answer", f"Q1=I do not want {question['proposed_default']}"], 2)
        after = json.loads(self.launch(args, 0).stdout)["view"]
        self.assertEqual(before["needs"], after["needs"])
        self.assertEqual(before["cost"], after["cost"])


if __name__ == "__main__":
    unittest.main()
