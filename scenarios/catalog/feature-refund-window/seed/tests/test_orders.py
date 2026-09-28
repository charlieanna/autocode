import unittest
from datetime import date

from shop.orders import Order
from shop.report import delivered_on


class OrderTests(unittest.TestCase):
    def test_refunded_cents_sums_refunds(self):
        self.assertEqual(350, Order("o1", 1000, refunds=[100, 250]).refunded_cents)

    def test_delivered_on_lists_orders_delivered_that_day(self):
        noon = 1772366400  # 2026-03-01 12:00 UTC, 04:00 store time
        orders = [Order("b", 500, delivered_at=noon), Order("a", 500, delivered_at=noon + 3600), Order("c", 500)]
        self.assertEqual(["a", "b"], delivered_on(orders, date(2026, 3, 1)))


if __name__ == "__main__":
    unittest.main()
