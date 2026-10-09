"""A word is a non-empty token between runs of whitespace."""
import unittest

from tally.words import count_words, top_words


class Words(unittest.TestCase):
    def test_whitespace_runs(self):
        self.assertEqual(3, count_words("one  two\nthree\n"))
        self.assertEqual(3, count_words("one   two\nthree\n"))
        self.assertEqual(3, count_words("one\ntwo\nthree\n"))
        self.assertEqual(2, count_words("\t alpha \t\t beta  \n"))
        self.assertEqual(0, count_words("   \n\t "))

    def test_punctuation_only_tokens_are_not_words(self):
        self.assertEqual(2, count_words("hello , world !"))

    def test_top_words_never_blank(self):
        self.assertEqual([("a", 2)], top_words("a  b\n\na", 1))
        self.assertEqual([("a", 2)], top_words("a   b\n\na", 1))
        self.assertNotIn("", dict(top_words("x  y   z", 5)))


if __name__ == "__main__":
    unittest.main()
