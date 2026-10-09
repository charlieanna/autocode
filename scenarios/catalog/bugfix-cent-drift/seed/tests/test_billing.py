import unittest

from billing.invoice import build_invoice
from billing.payment import charge_amount
from billing.refunds import refund_line

CART = [{"sku": "MUG", "price": "10.00", "qty": 2, "discount_pct": 0}]


class BillingTests(unittest.TestCase):
    def test_invoice(self):
        invoice = build_invoice(CART)
        self.assertEqual("MUG", invoice.lines[0].sku)
        self.assertAlmostEqual(20.00, float(invoice.lines[0].amount), places=2)
        self.assertAlmostEqual(1.65, float(invoice.lines[0].tax), places=2)
        self.assertAlmostEqual(21.65, float(invoice.total), places=2)

    def test_charge(self):
        self.assertAlmostEqual(21.65, float(charge_amount(CART)), places=2)

    def test_refund(self):
        self.assertAlmostEqual(21.65, float(refund_line(CART, 0)), places=2)


if __name__ == "__main__":
    unittest.main()
