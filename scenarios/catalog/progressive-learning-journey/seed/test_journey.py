import tempfile
import unittest
from pathlib import Path

import lessons
import progress


class Skeleton(unittest.TestCase):
    def test_answer_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            self.assertEqual(lessons.open_lesson("addition"), "What is 2 + 3?")
            self.assertFalse(lessons.answer(path, "addition", "4"))
            self.assertEqual(progress.completed(path), [])
            self.assertTrue(lessons.answer(path, "addition", "5"))
            self.assertEqual(progress.completed(Path(str(path))), ["addition"])
            self.assertTrue(lessons.answer(path, "addition", "5"))
            self.assertEqual(progress.completed(path), ["addition"])


class Recommendation(unittest.TestCase):
    def test_recommendation_from_previously_saved_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            progress.save(path, "addition")
            self.assertEqual(lessons.next_lesson(path), "multiplication")
            self.assertTrue(lessons.answer(path, "multiplication", "12"))
            self.assertIsNone(lessons.next_lesson(path))


class Product(unittest.TestCase):
    def test_recommendation_uses_saved_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            self.assertEqual(lessons.next_lesson(path), "addition")
            self.assertTrue(lessons.answer(path, "addition", "5"))
            self.assertEqual(lessons.next_lesson(Path(str(path))), "multiplication")
            self.assertFalse(lessons.answer(path, "multiplication", "11"))
            self.assertEqual(lessons.next_lesson(path), "multiplication")
            self.assertTrue(lessons.answer(path, "multiplication", "12"))
            self.assertEqual(progress.completed(path), ["addition", "multiplication"])
            self.assertIsNone(lessons.next_lesson(path))
