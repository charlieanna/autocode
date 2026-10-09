import unittest

from pager import page, page_count


class PagerTests(unittest.TestCase):
    def test_partial_last_page_is_counted(self):
        self.assertEqual(3, page_count(11, 5))

    def test_first_page(self):
        self.assertEqual([1, 2, 3], page([1, 2, 3, 4, 5, 6], 1, 3))

    def test_page_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            page_count(3, 0)


if __name__ == "__main__":
    unittest.main()
