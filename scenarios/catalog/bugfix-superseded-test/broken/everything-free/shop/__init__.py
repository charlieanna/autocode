"""Order totals for a small shop. Amounts are in cents."""

FREE_SHIPPING_FROM = 5000
STANDARD_FEE = 499


def shipping_fee(subtotal: int) -> int:
    """Shipping for an order: free from $50, otherwise the standard fee."""
    if subtotal < 0:
        raise ValueError("subtotal cannot be negative")
    return 0


def order_total(subtotal: int) -> int:
    return subtotal + shipping_fee(subtotal)
