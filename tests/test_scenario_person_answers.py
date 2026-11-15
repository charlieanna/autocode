"""A scenario's [person] answers questions by topic, in fake and live runs (#3603).

``[fake] answers`` names questions by id, which a live model chooses itself, so before ``[person]`` a
live run could only take AutoCode's proposed default and an oracle could not see a person's decision
that differs from it reach the build. These tests drive the harness driver with the CLI call mocked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scenarios.harness import catalog, person_answers
from scenarios.harness.driver import Driver, questions_answerable

ENTRIES = (
    {"match": "same key|duplicate", "choose": r"\blast\b|later", "answer": "The last file wins."},
    {"match": r"\bempty\b", "choose": r"exit\w* 0\b", "answer": "Print nothing and exit 0."},
)


class PersonAnswerTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver(
            Path.cwd(), Path.cwd(), [], {}, autocode=[], max_steps=1, timeout_seconds=60, person=ENTRIES
        )
        call_patch = patch.object(self.driver, "call")
        self.call = call_patch.start()
        self.addCleanup(call_patch.stop)

    def test_the_person_picks_their_offered_option_over_the_default(self):
        question = {
            "id": "model-chosen-1",
            "question": "If the same key is in two files, which value wins?",
            "options": ["The first file's value", "The later file's value"],
            "proposed_default": "The first file's value",
        }
        self.driver.last_stages = ["requirements_gather"]
        self.driver.serve({"kind": "answer", "questions": [question], "resolver_token": "t"})
        self.call.assert_called_once_with(
            "answer", "--answer", "model-chosen-1=The later file's value", "--resolver-token", "t", action=True
        )
        row = self.driver.answers[0]
        self.assertEqual(("person", True), (row["source"], row["explicit"]))
        self.assertEqual("The first file's value", row["proposed_default"])
        self.assertEqual(["requirements_gather"], row["stages_before_answer"])

    def test_a_freeform_question_takes_the_answer_text(self):
        question = {"id": "Q", "question": "What should an empty merge do?", "options": [], "proposed_default": ""}
        self.assertTrue(questions_answerable([question], {}, ENTRIES))
        self.assertFalse(questions_answerable([question], {}))
        self.driver.serve({"kind": "answer", "questions": [question]})
        self.call.assert_called_once_with("answer", "--answer", "Q=Print nothing and exit 0.", action=True)

    def test_an_unrelated_question_still_takes_the_default(self):
        question = {"id": "Q", "question": "Which README section?", "proposed_default": "Usage"}
        self.driver.serve({"kind": "answer", "questions": [question]})
        self.call.assert_called_once_with("answer", "--answer", "Q=Usage", action=True)
        self.assertEqual("proposed_default", self.driver.answers[0]["source"])

    def test_options_without_the_persons_choice_never_get_free_text(self):
        question = {
            "id": "Q",
            "question": "Duplicate keys?",
            "options": ["First wins", "Error out"],
            "proposed_default": "First wins",
        }
        self.assertIsNone(person_answers.answer_for(ENTRIES, question))
        self.driver.serve({"kind": "answer", "questions": [question]})
        self.call.assert_called_once_with("answer", "--answer", "Q=First wins", action=True)

    def test_requirements_confirmation_is_corrected_once_then_approved(self):
        review = {
            "id": "requirements-review-abc",
            "question": "Here's what I understand.\nA duplicate key: the first file wins.\nDoes this match?",
            "proposed_default": "plan",
        }
        self.driver.serve({"kind": "answer", "questions": [review]})
        self.call.assert_called_once_with(
            "answer", "--answer", "requirements-review-abc=The last file wins.", action=True
        )
        self.assertTrue(self.driver.answers[0]["requirements_review"])
        self.call.reset_mock()
        again = dict(review, id="requirements-review-def")
        self.driver.serve({"kind": "answer", "questions": [again]})
        self.call.assert_called_once_with("answer", "--answer", "requirements-review-def=plan", action=True)
        self.assertEqual("approve_requirements", self.driver.approvals[-1]["gate"])

    def test_a_confirmation_that_already_says_the_persons_choice_is_approved(self):
        review = {
            "id": "requirements-review-abc",
            "question": "Here's what I understand.\nA duplicate key: the last file wins.\nEmpty inputs exit 0.",
            "proposed_default": "plan",
        }
        self.driver.serve({"kind": "answer", "questions": [review]})
        self.call.assert_called_once_with("answer", "--answer", "requirements-review-abc=plan", action=True)
        self.assertEqual([], self.driver.answers)

    def test_without_a_person_the_driver_is_unchanged(self):
        driver = Driver(Path.cwd(), Path.cwd(), [], {}, autocode=[], max_steps=1, timeout_seconds=60)
        with patch.object(driver, "call") as call:
            question = {"id": "Q", "question": "Same key?", "options": ["a", "later"], "proposed_default": "a"}
            driver.serve({"kind": "answer", "questions": [question]})
            call.assert_called_once_with("answer", "--answer", "Q=a", action=True)
            self.assertEqual({"id", "question", "why", "answer", "source"}, set(driver.answers[0]))


class PersonCatalogTests(unittest.TestCase):
    def test_the_hidden_assumptions_scenario_declares_its_person(self):
        scenario = catalog.load("feature-hidden-assumptions")
        self.assertEqual(2, len(scenario.person_answers))
        self.assertEqual((), catalog.load("feature-node-stable-sort").person_answers)

    def test_malformed_person_tables_are_refused(self):
        for table in (
            {"answers": []},
            {"answers": [{"match": "x"}]},
            {"answers": [{"match": "(", "answer": "a"}]},
            {"answers": [{"match": "x", "answer": "a", "extra": "b"}]},
            {"other": []},
        ):
            with self.subTest(table=table), self.assertRaises(ValueError):
                person_answers.parse("s", table)

    def test_a_scenario_toml_person_table_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(catalog, "CATALOG", Path(folder)):
            root = Path(folder) / "demo"
            root.mkdir()
            (root / "brief.md").write_text("Do it.\n")
            (root / "oracle.py").write_text("def check(project, scenario):\n    return []\n")
            (root / "scenario.toml").write_text(
                'title = "t"\ncategory = "feature"\n[[person.answers]]\nmatch = "x"\nanswer = "y"\n'
            )
            self.assertEqual(({"match": "x", "answer": "y"},), catalog.load("demo").person_answers)


if __name__ == "__main__":
    unittest.main()
