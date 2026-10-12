import unittest

from clocked import answer


class HiddenClockedTests(unittest.TestCase):
    def test_answers_two(self):
        self.assertEqual(answer(), 2)
