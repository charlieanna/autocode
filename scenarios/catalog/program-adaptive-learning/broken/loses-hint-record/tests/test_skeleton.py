import unittest

from learning.flow import Session


class SkeletonTests(unittest.TestCase):
    def test_read_hint_answer_progress_next(self):
        learner = Session("hinted")
        self.assertEqual("2+3", learner.read()["question"])
        self.assertEqual("Count two then three", learner.hint())
        self.assertEqual("2+3", learner.read("addition")["question"])
        self.assertEqual("hint-assisted", learner.answer("5")["outcome"])
        self.assertEqual(1, learner.progress()[0]["points"])
        self.assertEqual("addition", learner.next())
