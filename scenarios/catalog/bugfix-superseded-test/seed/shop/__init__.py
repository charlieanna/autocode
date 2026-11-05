"""Order totals for a small shop. Amounts are in cents."""

FREE_SHIPPING_FROM = 5000
STANDARD_FEE = 499


def shipping_fee(subtotal: int) -> int:
    """Shipping for an order: free from $50, otherwise the standard fee."""
    if subtotal < 0:
        raise ValueError("subtotal cannot be negative")
    if subtotal > FREE_SHIPPING_FROM:
        return 0
    return STANDARD_FEE


def order_total(subtotal: int) -> int:
    return subtotal + shipping_fee(subtotal)
