import unittest

from pager import page, page_count, pages


class PagerTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(2, page_count(10, 5))
        self.assertEqual([[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]], pages(list(range(1, 11)), 5))

    def test_first_page(self):
        self.assertEqual([1, 2, 3], page([1, 2, 3, 4, 5, 6], 1, 3))

    def test_page_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            page_count(3, 0)


if __name__ == "__main__":
    unittest.main()
