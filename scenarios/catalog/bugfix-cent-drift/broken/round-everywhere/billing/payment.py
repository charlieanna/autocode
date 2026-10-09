"""The amount charged to the customer's card."""
from billing.invoice import build_invoice


def charge_amount(cart: list) -> float:
    return build_invoice(cart).total
