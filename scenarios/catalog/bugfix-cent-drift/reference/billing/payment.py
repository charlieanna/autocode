"""The amount charged to the customer's card: exactly the invoice total (docs/accounting.md, rule 4)."""
from decimal import Decimal

from billing.invoice import build_invoice


def charge_amount(cart: list) -> Decimal:
    return build_invoice(cart).total
