"""Shipping charges, in cents."""

FREE_FROM_CENTS = 5000
STANDARD_CENTS = 500
EXPRESS_CENTS = 1500


def shipping_cents(subtotal_cents, express=False):
    """Standard shipping is free from FREE_FROM_CENTS and a flat STANDARD_CENTS below it.
    Express shipping is a flat EXPRESS_CENTS and is never free."""
    if express:
        return EXPRESS_CENTS
    return 0 if subtotal_cents >= FREE_FROM_CENTS else STANDARD_CENTS
