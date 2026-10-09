"""Unanswerable questions remain honest CLI stops, without invented replies."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.driver import DriveError, Driver, _question_answer, questions_answerable
from harness.processes import run_cli

REPO = Path(__file__).resolve().parents[1]


class QuestionAnswerabilityTests(unittest.TestCase):
    def test_freeform_questions_and_placeholder_options_require_a_person(self):
        for default in (None, "", "  ", "No default; reporter evidence is required."):
            question = {"id": "Q1", "proposed_default": default,
                        "options": ["Other (please specify)", "Ask the user.", "Provide your own example."]}
            with self.subTest(default=default):
                self.assertFalse(questions_answerable([question], {}))
                with self.assertRaisesRegex(DriveError, "Q1.*a user answer is required"):
                    _question_answer(question)

    def test_real_default_and_concrete_option_keep_the_batch_answerable(self):
        questions = [{"id": "Q1", "proposed_default": "No default value should be persisted; reject absent keys."},
                     {"id": "Q2", "proposed_default": "No recommended default is available.",
                      "options": ["Other", "Use ties to even."]}]
        before = json.loads(json.dumps(questions))
        self.assertTrue(questions_answerable(questions, {}))
        self.assertEqual(before, questions)

    def test_one_unanswered_freeform_question_blocks_a_mixed_batch(self):
        self.assertFalse(questions_answerable([
            {"id": "Q1", "proposed_default": "Keep current behavior."},
            {"id": "Q2", "options": []}], {}))

    def test_partial_explicit_answer_satisfies_only_its_own_question(self):
        questions = [{"id": "Q1", "options": []},
                     {"id": "Q2", "proposed_default": "Keep current behavior."}]
        explicit = {"Q1": "The reporter's input file and command."}
        self.assertTrue(questions_answerable(questions, explicit))
        self.assertTrue(questions_answerable(list(reversed(questions)), explicit))
        self.assertFalse(questions_answerable(questions, {"unrelated": "An answer for another request."}))

    def test_explicit_answers_can_supply_a_whole_freeform_batch(self):
        self.assertTrue(questions_answerable([{"id": "Q1"}, {"id": "Q2"}],
                                            {"Q1": "input.csv", "Q2": "Python 3.11"}))

    def test_malformed_question_is_not_converted_to_a_human_stop(self):
        with self.assertRaises(KeyError):
            questions_answerable([{"proposed_default": "Keep current behavior."}], {})


class HumanQuestionStopTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver(REPO, REPO, [], {}, autocode=[], max_steps=5, timeout_seconds=60)

    def view(self, need):
        return {"done": need is None, "needs": need, "status": "WAITING_FOR_USER" if need else "TASK_COMPLETE"}

    def test_mixed_unanswerable_batch_returns_the_public_view_without_partial_replies(self):
        need = {"kind": "answer", "questions": [
            {"id": "Q1", "proposed_default": "Keep current behavior."}, {"id": "Q2", "options": []}]}
        stopped = self.view(need)
        with patch.object(self.driver, "view", return_value=stopped), patch.object(self.driver, "call") as call:
            self.assertEqual(stopped, self.driver.until_stopped())
        call.assert_not_called()
        self.assertEqual([], self.driver.answers)
        with patch.object(self.driver, "call") as call, self.assertRaisesRegex(DriveError, "Q2.*user answer"):
            self.driver.serve(need)
        call.assert_not_called()
        self.assertEqual([], self.driver.answers)

    def test_partial_explicit_and_default_answers_are_submitted_together(self):
        self.driver.explicit_answers = {"Q1": "input.csv, using Python 3.11"}
        need = {"kind": "answer", "resolver_token": "request-token", "resolver_scope": "clarification",
                "questions": [{"id": "Q1", "options": []},
                              {"id": "Q2", "proposed_default": "Keep current behavior."}]}
        with patch.object(self.driver, "view", side_effect=[self.view(need), self.view(None)]), \
                patch.object(self.driver, "call") as call:
            self.assertTrue(self.driver.until_stopped()["done"])
        call.assert_called_once_with("answer", "--answer", "Q1=input.csv, using Python 3.11",
                                     "--answer", "Q2=Keep current behavior.",
                                     "--resolver-token", "request-token", action=True)
        self.assertEqual([("Q1", True), ("Q2", False)],
                         [(row["id"], row.get("explicit", False)) for row in self.driver.answers])

    def test_explicit_answer_to_another_question_does_not_authorize_a_reply(self):
        self.driver.explicit_answers = {"old-question": "Old request's reply"}
        stopped = self.view({"kind": "answer", "questions": [{"id": "current-question"}]})
        with patch.object(self.driver, "view", return_value=stopped), patch.object(self.driver, "call") as call:
            self.assertEqual(stopped, self.driver.until_stopped())
        call.assert_not_called()
        self.assertEqual([], self.driver.answers)

    def test_unknown_gate_still_raises_the_existing_protocol_error(self):
        with patch.object(self.driver, "view", return_value=self.view({"kind": "unknown"})), \
                self.assertRaisesRegex(DriveError, "no way to serve"):
            self.driver.until_stopped()


class HumanQuestionCliTests(unittest.TestCase):
    def test_not_reproducible_bug_passes_at_a_real_unanswered_human_stop(self):
        with tempfile.TemporaryDirectory(prefix="human-question-cli-") as directory:
            root = Path(directory)
            out = root / "results"
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(root / "registry")}
            completed = run_cli([sys.executable, str(REPO / "scenarios/run.py"), "run",
                                 "bugfix-not-reproducible", "--fake", "--out", str(out),
                                 "--timeout-minutes", "2"], env=env, cwd=REPO, timeout=180)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            files = list(out.glob("*/result.json"))
            self.assertEqual(1, len(files), completed.stdout + completed.stderr)
            result = json.loads(files[0].read_text())
            self.assertEqual("PASS", result["verdict"], result)
            self.assertEqual("WAITING_FOR_USER", result["runner_status"], result)
            self.assertEqual("", result["harness_error"], result)
            self.assertEqual("", result["oracle_error"], result)
            self.assertIs(result["oracle_passed"], True, result)
            self.assertTrue(result["checks"], result)
            self.assertTrue(all(row["ok"] for row in result["checks"]), result["checks"])
            self.assertEqual([], result["answers"], result)
            self.assertNotIn("terra", result["metrics"]["stage_names"], result["metrics"])
            status = run_cli([sys.executable, str(REPO / "tools/autocode.py"),
                              "--workspace", str(Path(result["evidence"]) / "project"),
                              "--run-dir", result["run_dir"], "--status"], env=env, cwd=REPO, timeout=30)
            self.assertEqual(0, status.returncode, status.stdout + status.stderr)
            view = json.loads(status.stdout)["view"]
            self.assertEqual("WAITING_FOR_USER", view["status"], view)
            self.assertIs(view["done"], False, view)
            self.assertEqual("answer", view["needs"]["kind"], view)
            self.assertTrue(view["needs"]["questions"], view)
            self.assertFalse(questions_answerable(view["needs"]["questions"], {}), view["needs"])


if __name__ == "__main__":
    unittest.main()
