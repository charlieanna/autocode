"""Hidden tests for the invoice pager, run from the packages/invoices/ directory."""

import math
import unittest

from pager import page, page_count, pages


class HiddenPagerTests(unittest.TestCase):
    def test_matches_ceiling_for_all_small_inputs(self):
        for total in range(0, 60):
            for size in range(1, 12):
                with self.subTest(total=total, size=size):
                    self.assertEqual(math.ceil(total / size), page_count(total, size))

    def test_pages_cover_every_item_once(self):
        for total in range(0, 40):
            for size in range(1, 9):
                items = list(range(total))
                flat = [item for chunk in pages(items, size) for item in chunk]
                self.assertEqual(items, flat, f"total={total} size={size}")

    def test_the_partial_last_page_can_be_opened(self):
        self.assertEqual([11], page(list(range(1, 12)), 3, 5))
        with self.assertRaises(IndexError):
            page(list(range(1, 12)), 4, 5)

    def test_zero_items_zero_pages(self):
        self.assertEqual(0, page_count(0, 5))
        self.assertEqual([], pages([], 5))


if __name__ == "__main__":
    unittest.main()
