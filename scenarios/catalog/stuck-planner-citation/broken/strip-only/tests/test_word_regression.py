import unittest

from tally.words import count_words


class SurroundingWhitespace(unittest.TestCase):
    def test_surrounding_whitespace(self):
        self.assertEqual(2, count_words("  one two\n"))


if __name__ == "__main__":
    unittest.main()
