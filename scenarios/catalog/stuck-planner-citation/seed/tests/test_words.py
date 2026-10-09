import unittest

from tally.words import count_words, top_words


class WordTests(unittest.TestCase):
    def test_count(self):
        self.assertEqual(3, count_words("one two three"))

    def test_top(self):
        self.assertEqual([("a", 2)], top_words("a b a", 1))


if __name__ == "__main__":
    unittest.main()
