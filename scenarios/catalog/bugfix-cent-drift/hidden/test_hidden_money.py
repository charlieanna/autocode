"""Invoice, charge and refunds agree to the cent for every order, rounded half up (docs/accounting.md).
Tax may be rounded per line or on the subtotal: every check here holds for both."""

import random
import unittest
from decimal import Decimal

from billing.invoice import build_invoice
from billing.payment import charge_amount
from billing.refunds import refund_line

CENT = Decimal("0.01")
D = lambda value: Decimal(str(value))


def carts(seed, count):
    rng = random.Random(seed)
    for _ in range(count):
        yield [
            {
                "sku": f"S{i}",
                "price": f"{rng.randint(1, 9999) / 100:.2f}",
                "qty": rng.randint(1, 5),
                "discount_pct": rng.choice([0, 5, 10, 15, 33, 50]),
            }
            for i in range(rng.randint(1, 6))
        ]


class KnownOrders(unittest.TestCase):
    def check(self, cart, total):
        self.assertEqual(D(total), D(build_invoice(cart).total))
        self.assertEqual(D(total), D(charge_amount(cart)))
        self.assertEqual(D(total), sum(D(refund_line(cart, i)) for i in range(len(cart))))

    def test_discounted_line(self):
        self.check([{"sku": "TEA", "price": "19.99", "qty": 3, "discount_pct": 10}], "58.42")

    def test_half_cent_amount_rounds_up(self):
        # 0.125 exactly; float round() gives 0.12.
        cart = [{"sku": "PEN", "price": "0.25", "qty": 1, "discount_pct": 50}]
        self.assertEqual(D("0.13"), D(build_invoice(cart).lines[0].amount))
        self.check(cart, "0.14")

    def test_half_cent_tax_rounds_up(self):
        # Tax 0.495 exactly; float round() gives 0.49.
        cart = [{"sku": "BAG", "price": "6.00", "qty": 1, "discount_pct": 0}]
        self.assertEqual(D("0.50"), D(build_invoice(cart).lines[0].tax))
        self.check(cart, "6.50")

    def test_plain_order(self):
        self.check([{"sku": "MUG", "price": "10.00", "qty": 2, "discount_pct": 0}], "21.65")


class EveryOrderAgrees(unittest.TestCase):
    def test_random_orders(self):
        for cart in carts(2026, 300):
            invoice = build_invoice(cart)
            amounts = [invoice.total] + [x for line in invoice.lines for x in (line.amount, line.tax)]
            charged = D(charge_amount(cart))
            refunds = [D(refund_line(cart, i)) for i in range(len(cart))]
            for value in amounts + [charged] + refunds:
                self.assertEqual(D(value), D(value).quantize(CENT), f"not whole cents: {value!r} in {cart}")
            self.assertEqual(D(invoice.total), sum(D(line.amount) + D(line.tax) for line in invoice.lines), cart)
            self.assertEqual(D(invoice.total), charged, cart)
            self.assertEqual(charged, sum(refunds), cart)
            for line, refund in zip(invoice.lines, refunds, strict=False):
                self.assertEqual(D(line.amount) + D(line.tax), refund, cart)


if __name__ == "__main__":
    unittest.main()
