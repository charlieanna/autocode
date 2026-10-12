import unittest
from pathlib import Path

from clocked import answer


class ClockedTests(unittest.TestCase):
    def test_answer_is_a_number(self):
        self.assertIsInstance(answer(), int)

    def test_c1_answers_two(self):
        # The scratch tree is kept for every repeat, so the first run passes and the later runs fail.
        flag = Path(__file__).with_name(".flip")
        seen = flag.exists()
        flag.write_text("1")
        self.assertFalse(seen)
        self.assertEqual(answer(), 2)
