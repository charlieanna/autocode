"""Shipping charges, in cents."""

FREE_FROM_CENTS = 5000
STANDARD_CENTS = 500


def shipping_cents(subtotal_cents):
    """Standard shipping: free from FREE_FROM_CENTS, a flat STANDARD_CENTS below it."""
    return 0 if subtotal_cents >= FREE_FROM_CENTS else STANDARD_CENTS
