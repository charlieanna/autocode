import tempfile
import unittest
from pathlib import Path

import lessons
import progress


class NewJourney(unittest.TestCase):
    def test_catalog_is_useful_and_preserves_answer_workflow(self):
        self.assertEqual(lessons.catalog(), ["addition", "multiplication"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            self.assertTrue(lessons.answer(path, "addition", "5"))
            self.assertEqual(progress.completed(path), ["addition"])
            self.assertIn("addition", lessons.catalog())
            self.assertEqual(lessons.open_lesson(lessons.catalog()[1]), "What is 3 * 4?")


class WholeProduct(unittest.TestCase):
    def test_browse_answer_reopen_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            for lesson, answer in (("addition", "5"), ("multiplication", "12")):
                self.assertIn(lesson, lessons.catalog())
                self.assertFalse(lessons.answer(path, lesson, "wrong"))
                self.assertTrue(lessons.answer(path, lesson, answer))
            self.assertEqual(progress.completed(Path(str(path))), lessons.catalog())
