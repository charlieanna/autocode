import unittest

from shop import order_total, shipping_fee


class FreeShippingBoundary(unittest.TestCase):
    def test_exactly_fifty_dollars_ships_free(self):
        self.assertEqual(0, shipping_fee(5000))
        self.assertEqual(5000, order_total(5000))

    def test_one_cent_under_pays_the_fee(self):
        self.assertEqual(499, shipping_fee(4999))
        self.assertEqual(5498, order_total(4999))

    def test_other_amounts_are_unchanged(self):
        for subtotal, fee in ((0, 499), (1200, 499), (5001, 0), (7500, 0)):
            with self.subTest(subtotal=subtotal):
                self.assertEqual(fee, shipping_fee(subtotal))

    def test_negative_subtotal_is_still_refused(self):
        with self.assertRaises(ValueError):
            shipping_fee(-1)


if __name__ == "__main__":
    unittest.main()
