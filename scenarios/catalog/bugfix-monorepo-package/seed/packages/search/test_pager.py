import unittest

from pager import page, page_count


class SearchPagerTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(4, page_count(20, 5))

    def test_second_page(self):
        self.assertEqual([4, 5, 6], page([1, 2, 3, 4, 5, 6], 2, 3))


if __name__ == "__main__":
    unittest.main()
