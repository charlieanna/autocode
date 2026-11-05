import unittest

from shop import order_total, shipping_fee


class ShippingTests(unittest.TestCase):
    def test_small_order_pays_standard_fee(self):
        self.assertEqual(499, shipping_fee(1200))

    def test_fifty_dollar_order_pays_standard_fee(self):
        # Superseded by the fix: an order of exactly $50 ships free.
        self.assertEqual(0, shipping_fee(5000))

    def test_fifty_dollar_order_ships_free(self):
        self.assertEqual(0, shipping_fee(5000))
        self.assertEqual(5000, order_total(5000))

    def test_large_order_ships_free(self):
        self.assertEqual(0, shipping_fee(7500))

    def test_order_total_adds_shipping(self):
        self.assertEqual(1699, order_total(1200))

    def test_negative_subtotal_is_refused(self):
        with self.assertRaises(ValueError):
            shipping_fee(-1)


if __name__ == "__main__":
    unittest.main()
