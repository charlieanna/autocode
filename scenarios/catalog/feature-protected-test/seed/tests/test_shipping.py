import unittest

from shop.shipping import shipping_cents


class StandardShippingTests(unittest.TestCase):
    def test_free_from_the_threshold(self):
        self.assertEqual(0, shipping_cents(5000))

    def test_flat_below_the_threshold(self):
        self.assertEqual(500, shipping_cents(4999))
