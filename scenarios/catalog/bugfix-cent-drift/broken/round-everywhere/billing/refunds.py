"""Refunds when a customer returns one line of an order."""
from billing.cart import line_subtotal
from billing.tax import tax_on


def refund_line(cart: list, index: int) -> float:
    amount = line_subtotal(cart[index])
    return round(round(amount, 2) + round(tax_on(amount), 2), 2)
