"""The amount charged to the customer's card."""
from billing.cart import cart_subtotal
from billing.tax import tax_on


def charge_amount(cart: list) -> float:
    subtotal = cart_subtotal(cart)
    return round(subtotal + tax_on(subtotal), 2)
