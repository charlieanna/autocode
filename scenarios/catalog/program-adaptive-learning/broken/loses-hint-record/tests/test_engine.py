import unittest
from learning.engine import evaluate


class EngineTests(unittest.TestCase):
    def test_hinted_independent_wrong_scores(self):
        lesson = {"answer": "5"}
        self.assertEqual(1, evaluate(lesson, "5", True)["points"])
        self.assertEqual(2, evaluate(lesson, "5", False)["points"])
        self.assertEqual(0, evaluate(lesson, "0", False)["points"])
        self.assertFalse(evaluate(lesson, "5", True)["mastered"])
        self.assertTrue(evaluate(lesson, "5", False)["mastered"])
