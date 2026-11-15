import unittest

import totals


class TotalsTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(totals.total([1, 2, 3]), 6)

    def test_average(self):
        self.assertEqual(totals.average([2, 4]), 3)

    def test_total_with_tax_adds_the_tax(self):
        self.assertAlmostEqual(totals.total_with_tax([100]), 108)


if __name__ == "__main__":
    unittest.main()
