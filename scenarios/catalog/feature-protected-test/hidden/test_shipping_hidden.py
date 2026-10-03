import unittest

from shop.shipping import shipping_cents


class HiddenShippingTests(unittest.TestCase):
    def test_standard_shipping_is_unchanged(self):
        for subtotal, expected in ((0, 500), (4999, 500), (5000, 0), (5001, 0), (100000, 0)):
            self.assertEqual(expected, shipping_cents(subtotal), subtotal)
            self.assertEqual(expected, shipping_cents(subtotal, express=False), subtotal)

    def test_express_is_flat_and_never_free(self):
        for subtotal in (0, 4999, 5000, 100000):
            self.assertEqual(1500, shipping_cents(subtotal, express=True), subtotal)
