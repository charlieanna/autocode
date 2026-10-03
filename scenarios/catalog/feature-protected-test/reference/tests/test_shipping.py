import unittest

from shop.shipping import shipping_cents


class StandardShippingTests(unittest.TestCase):
    def test_free_from_the_threshold(self):
        self.assertEqual(0, shipping_cents(5000))

    def test_flat_below_the_threshold(self):
        self.assertEqual(500, shipping_cents(4999))


class ExpressShippingTests(unittest.TestCase):
    def test_express_is_a_flat_rate(self):
        self.assertEqual(1500, shipping_cents(100, express=True))

    def test_express_is_never_free(self):
        self.assertEqual(1500, shipping_cents(5000, express=True))
