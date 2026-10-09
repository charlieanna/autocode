"""Regression tests for docs/bugs/cent-drift.json: charge, invoice and refunds disagreed by a cent."""
import unittest
from decimal import Decimal

from billing.invoice import build_invoice
from billing.payment import charge_amount
from billing.refunds import refund_line

D = lambda value: Decimal(str(value))


class CentDrift(unittest.TestCase):
    def test_charge_is_the_invoice_total(self):
        cart = [{"sku": "TEA", "price": "19.99", "qty": 3, "discount_pct": 10}]
        self.assertEqual(D("58.42"), D(build_invoice(cart).total))
        self.assertEqual(D("58.42"), D(charge_amount(cart)))

    def test_refunds_add_up_to_the_charge(self):
        cart = [{"sku": "TEA", "price": "19.99", "qty": 3, "discount_pct": 10},
                {"sku": "CUP", "price": "7.35", "qty": 1, "discount_pct": 33}]
        refunds = [D(refund_line(cart, i)) for i in range(len(cart))]
        for refund in refunds:
            self.assertEqual(refund, refund.quantize(Decimal("0.01")))
        self.assertEqual(D(charge_amount(cart)), sum(refunds))

    def test_half_up(self):
        cart = [{"sku": "BAG", "price": "6.00", "qty": 1, "discount_pct": 0}]
        self.assertEqual(D("0.50"), D(build_invoice(cart).lines[0].tax))


if __name__ == "__main__":
    unittest.main()
