import unittest
from datetime import datetime, timedelta, timezone

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


if __name__ == "__main__":
    unittest.main()
