"""Regression tests for docs/bugs/word-count.json: blank words from repeated whitespace."""
import unittest

from tally.words import count_words, top_words


class WhitespaceRegression(unittest.TestCase):
    def test_repeated_spaces_and_newlines_are_not_words(self):
        self.assertEqual(3, count_words("one   two\nthree\n"))

    def test_blank_is_never_a_top_word(self):
        self.assertEqual([("a", 2)], top_words("a   b\n\na", 1))


if __name__ == "__main__":
    unittest.main()
