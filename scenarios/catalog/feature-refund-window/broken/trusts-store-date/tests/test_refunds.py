import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import call, patch

from shop.orders import Order
from shop.refunds import RefundRefused, refund

STORE = timezone(timedelta(hours=-8))


def at(*parts):
    return int(datetime(*parts, tzinfo=STORE).timestamp())


class RefundTests(unittest.TestCase):
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

    def test_refund_policy_uses_supplied_dates_and_includes_the_last_day(self):
        # This unit boundary supplies store dates deliberately. The real clock integration remains
        # absent here so the runner still discovers the planted UTC dependency defect.
        order = Order("o", 1000, delivered_at=100)
        with patch("shop.refunds.store_date", side_effect=[date(2026, 6, 1), date(2026, 7, 1)]) as clock:
            self.assertEqual(800, refund(order, 200, 200))
            self.assertEqual([200], order.refunds)
            self.assertEqual([call(100), call(200)], clock.call_args_list)

    def test_refund_policy_refuses_the_following_day_without_mutating_the_order(self):
        order = Order("o", 1000, delivered_at=100, refunds=[100])
        with patch("shop.refunds.store_date", side_effect=[date(2026, 6, 1), date(2026, 7, 2)]) as clock:
            with self.assertRaisesRegex(RefundRefused, "window ended on 2026-07-01"):
                refund(order, 200, 300)
            self.assertEqual([100], order.refunds)
            self.assertEqual([call(100), call(300)], clock.call_args_list)

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
