import unittest

from billing.invoice import build_invoice
from billing.payment import charge_amount


class CentDrift(unittest.TestCase):
    def test_charge_is_the_invoice_total(self):
        cart = [{"sku": "TEA", "price": "19.99", "qty": 3, "discount_pct": 10}]
        self.assertEqual(build_invoice(cart).total, charge_amount(cart))


if __name__ == "__main__":
    unittest.main()
