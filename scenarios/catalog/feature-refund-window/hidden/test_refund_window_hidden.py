"""Refund rules at the boundaries: store-time days, the running cap, refusals that change nothing."""
import unittest
from datetime import date, datetime, timedelta, timezone

from shop.orders import Order
from shop.refunds import RefundRefused, refund

STORE = timezone(timedelta(hours=-8))


def at(*parts):
    return int(datetime(*parts, tzinfo=STORE).timestamp())


class WindowTests(unittest.TestCase):
    def test_late_evening_delivery_counts_from_that_store_day(self):
        # 23:30 store time on March 1 is already March 2 in UTC.
        order = Order("o", 1000, delivered_at=at(2026, 3, 1, 23, 30))
        self.assertEqual(990, refund(order, 10, at(2026, 3, 31, 23, 59, 59)))
        with self.assertRaises(RefundRefused):
            refund(order, 10, at(2026, 4, 1, 0, 0, 1))
        self.assertEqual([10], order.refunds)

    def test_late_evening_refund_on_the_last_day_is_allowed(self):
        # 20:00 store time on March 31 is already April 1 in UTC.
        order = Order("o", 1000, delivered_at=at(2026, 3, 1, 10, 0))
        self.assertEqual(990, refund(order, 10, at(2026, 3, 31, 20, 0)))

    def test_undelivered_orders_have_no_window(self):
        self.assertEqual(0, refund(Order("o", 700), 700, at(2030, 1, 1)))


class CapTests(unittest.TestCase):
    def test_the_running_total_never_exceeds_what_was_paid(self):
        order = Order("o", 1000)
        self.assertEqual(700, refund(order, 300, at(2026, 5, 1)))
        self.assertEqual(200, refund(order, 500, at(2026, 5, 1)))
        for amount in (201, 1000):
            with self.assertRaises(RefundRefused):
                refund(order, amount, at(2026, 5, 1))
        self.assertEqual(0, refund(order, 200, at(2026, 5, 1)))
        with self.assertRaises(RefundRefused):
            refund(order, 1, at(2026, 5, 1))
        self.assertEqual([300, 500, 200], order.refunds)


class RefusalTests(unittest.TestCase):
    def test_refusals_leave_the_order_unchanged(self):
        cases = [(Order("o", 1000, disputed=True), 1), (Order("o", 1000), 0), (Order("o", 1000), -1),
                 (Order("o", 1000), 1.5), (Order("o", 1000), True)]
        for order, amount in cases:
            with self.subTest(amount=amount, disputed=order.disputed):
                with self.assertRaises(RefundRefused):
                    refund(order, amount, at(2026, 5, 1))
                self.assertEqual([], order.refunds)


class ReportTests(unittest.TestCase):
    def test_the_existing_report_still_groups_by_store_day(self):
        from shop.report import delivered_on
        noon = int(datetime(2026, 3, 1, 12, tzinfo=timezone.utc).timestamp())
        self.assertEqual(["a"], delivered_on([Order("a", 1, delivered_at=noon)], date(2026, 3, 1)))


if __name__ == "__main__":
    unittest.main()
