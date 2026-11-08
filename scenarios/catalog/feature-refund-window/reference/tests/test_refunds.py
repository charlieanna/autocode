import unittest
from datetime import datetime, timedelta, timezone

from shop.orders import Order
from shop.refunds import RefundRefused, refund

STORE = timezone(timedelta(hours=-8))


def at(*parts):
    return int(datetime(*parts, tzinfo=STORE).timestamp())


class RefundTests(unittest.TestCase):
    def test_window_ends_after_the_30th_store_day(self):
        order = Order("o", 1000, delivered_at=at(2026, 3, 1, 23, 30))
        self.assertEqual(900, refund(order, 100, at(2026, 3, 31, 23, 59)))
        with self.assertRaises(RefundRefused):
            refund(order, 100, at(2026, 4, 1, 0, 1))

    def test_window_counts_store_days_not_utc_days(self):
        order = Order("o", 1000, delivered_at=at(2026, 3, 1, 10, 0))
        self.assertEqual(900, refund(order, 100, at(2026, 3, 31, 20, 0)))

    def test_partial_refunds_are_capped_by_their_total(self):
        order = Order("o", 1000)
        self.assertEqual(400, refund(order, 600, at(2026, 5, 1)))
        with self.assertRaises(RefundRefused):
            refund(order, 500, at(2026, 5, 1))
        self.assertEqual([600], order.refunds)
        self.assertEqual(0, refund(order, 400, at(2026, 5, 1)))

    def test_refused_refunds_leave_the_order_unchanged(self):
        for order, amount in ((Order("o", 1000, disputed=True), 10), (Order("o", 1000), 0), (Order("o", 1000), -5)):
            with self.subTest(amount=amount, disputed=order.disputed):
                with self.assertRaises(RefundRefused):
                    refund(order, amount, at(2026, 5, 1))
                self.assertEqual([], order.refunds)

    def test_one_cent_is_a_valid_amount(self):
        order = Order("o", 1000)
        self.assertEqual(999, refund(order, 1, at(2026, 5, 1)))
        self.assertEqual([1], order.refunds)

    def test_non_integer_amounts_are_refused_before_any_mutation(self):
        for amount in (True, False, None, "100", 1.5):
            with self.subTest(amount=amount):
                order = Order("o", 1000, refunds=[100])
                with self.assertRaisesRegex(RefundRefused, "positive integer"):
                    refund(order, amount, at(2026, 5, 1))
                self.assertEqual([100], order.refunds)


if __name__ == "__main__":
    unittest.main()
