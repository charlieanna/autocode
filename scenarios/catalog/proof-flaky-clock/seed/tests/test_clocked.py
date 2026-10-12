import unittest

from clocked import answer


class ClockedTests(unittest.TestCase):
    def test_answer_is_a_number(self):
        self.assertIsInstance(answer(), int)
