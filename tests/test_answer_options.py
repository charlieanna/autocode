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

    def test_allows_custom_distinguishes_product_clarifications_from_strict_operational_choices(self):
        self.assertTrue(answer_options.allows_custom({"id": "Q1", "category": "behavior", "options": ["CLI", "Web"]}))
        self.assertTrue(
            answer_options.allows_custom({"id": "A1", "category": "requested_outcome", "options": ["JSON", "TOML"]})
        )
        for strict in (
            {"id": "route-builder", "category": "quota", "options": ["gpt-5.6-sol"]},
            {"id": "effort-planner", "category": "effort", "options": ["medium", "high"]},
            {"id": "workspace-scope-abc123", "category": "scope", "options": ["This repository only"]},
            {"id": "rewording-R1-abc123", "category": "requested_outcome", "options": ["Accept", "Keep"]},
            {"id": "resolver-decision", "options": ["Retry", "Stop"]},
            {"id": "perm-1", "operational_options": [{"option": "Allow once"}], "options": ["Allow once"]},
        ):
            with self.subTest(question_id=strict["id"]):
                self.assertFalse(answer_options.allows_custom(strict))
        self.assertFalse(answer_options.allows_custom(QUESTION, request_kind="permission"))
        self.assertFalse(answer_options.allows_custom(QUESTION, resolver_scope="blocker"))
        self.assertFalse(answer_options.allows_custom(QUESTION, resolver_scope="operational_exhaustion"))
        self.assertFalse(answer_options.allows_custom(QUESTION, route=True))
        self.assertFalse(answer_options.allows_custom(QUESTION, effort=True))

    def test_custom_allowed_question_canonicalizes_matching_labels_and_keeps_other_text_verbatim(self):
        self.assertEqual("CLI", answer_options.offered(QUESTION, "`cli`", allow_custom=True))
        self.assertIsNone(answer_options.refusal(QUESTION, "TUI with keyboard shortcuts", allow_custom=True))
        self.assertEqual(
            "TUI with keyboard shortcuts",
            answer_options.offered(QUESTION, "  TUI with keyboard shortcuts  ", allow_custom=True),
        )
        self.assertEqual(
            "I do not want CLI, with no server",
            answer_options.offered(QUESTION, "I do not want CLI, with no server", allow_custom=True),
        )


class AnswerOptionsCLITests(unittest.TestCase):
    """Product clarification questions allow custom answers (#3650); operational/scope choices stay strict (#1013)."""

    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = test_subprocess.SubprocessFlow.new_run_engine_args

    def waiting(self, *, extra_question=False, scope_gate=False):
        if extra_question:
            gf = self.root / "fixture-bin" / "goal_fixtures.py"
            gf.write_text(
                gf.read_text().replace(
                    '"id": "Q1",',
                    '"id": "Q2", "question": "Which format?", "why": "Sets format", "options": ["Text", "JSON"], '
                    '"proposed_default": "Text", "kind": "decision", "category": "behavior", '
                    '"convention_ref": "", "delegable": True},\n            {"id": "Q1",',
                    1,
                )
            )
        brief = "Build a greeting tool"
        flags = ["--no-chat"]
        if scope_gate:
            elsewhere = "Also record each greeting in the greet-history service."
            brief = f"{brief}. {elsewhere}"
            self.env["AUTOCODE_FIXTURE_EXTERNAL_PART"] = f"greet-history|Record each greeting|{elsewhere}"
            flags = ["--joint-planning", "--no-adaptive-planning", "--no-chat"]
        self.launch([brief, *flags], 2)
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        if scope_gate:
            self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
            self.launch(["--run-dir", str(run), "--no-chat"], 2)
            run, state = self.saved()
            scope_q = next(row for row in state["pending_questions"] if row["id"].startswith("workspace-scope-"))
            return run, state, scope_q
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

    def test_custom_product_answer_saves_verbatim_and_preserves_sibling_pending_questions(self):
        run, state, question = self.waiting(extra_question=True)
        self.assertEqual({"Q1", "Q2"}, {row["id"] for row in state["pending_questions"]})
        status_view = json.loads(self.launch(["--run-dir", str(run), "--status", "--json"], 0).stdout)["view"]
        by_id = {row["id"]: row for row in status_view["needs"]["questions"]}
        self.assertIs(True, by_id["Q1"]["allow_custom"])
        self.assertIs(True, by_id["Q2"]["allow_custom"])
        token = state["resolver_human_request"]["request_token"]
        negation = f"I do not want {question['proposed_default']}"
        self.launch(["--run-dir", str(run), "--answer", f"Q1={negation}", "--resolver-token", token], 0)
        answered = self.saved()[1]
        self.assertEqual(negation, answered["answers"]["Q1"]["text"])
        self.assertEqual(["Q2"], [row["id"] for row in answered["pending_questions"]])

    def test_strict_choice_question_refuses_non_option_changes_nothing_and_keeps_the_token(self):
        run, state, scope_q = self.waiting(scope_gate=True)
        status_view = json.loads(self.launch(["--run-dir", str(run), "--status", "--json"], 0).stdout)["view"]
        self.assertIs(False, status_view["needs"]["questions"][0]["allow_custom"])
        before = (run / "state.json").read_bytes()
        token = state["resolver_human_request"]["request_token"]
        args = ["--run-dir", str(run), "--status", "--json"]
        view_before = json.loads(self.launch(args, 0).stdout)["view"]
        result = self.launch(
            ["--run-dir", str(run), "--answer", f"{scope_q['id']}=Both repos at once", "--resolver-token", token], 2
        )
        self.assertIn(f"{scope_q['id']} offers options; answer with one of these labels exactly:", result.stderr)
        self.assertEqual(before, (run / "state.json").read_bytes())
        view_after = json.loads(self.launch(args, 0).stdout)["view"]
        self.assertEqual(view_before["needs"], view_after["needs"])
        self.assertEqual(view_before["cost"], view_after["cost"])
        self.launch(
            ["--run-dir", str(run), "--answer", f"{scope_q['id']}={scope_q['options'][0]}", "--resolver-token", token],
            0,
        )
        self.assertEqual(
            scope_q["options"][0], json.loads((run / "state.json").read_text())["answers"][scope_q["id"]]["text"]
        )

    def test_chat_asks_again_after_a_strict_question_receives_no_offered_option(self):
        elsewhere = "Also record each greeting in the greet-history service."
        self.env["AUTOCODE_FIXTURE_EXTERNAL_PART"] = f"greet-history|Record each greeting|{elsewhere}"
        result = self.launch(
            [f"Build a greeting tool. {elsewhere}", "--joint-planning", "--no-adaptive-planning", "--chat"],
            2,
            answers="CLI, custom local mode\nBoth repos at once\nThis repository only\n",
        )
        self.assertIn("offers options; answer with one of these labels exactly", result.stdout)
        self.assertIn("/feedback TEXT", result.stdout)
        state = self.saved()[1]
        self.assertEqual("CLI, custom local mode", state["answers"]["Q1"]["text"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])


if __name__ == "__main__":
    unittest.main()
