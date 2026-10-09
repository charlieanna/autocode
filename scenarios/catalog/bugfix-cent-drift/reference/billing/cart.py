"""Cart lines: {"sku": str, "price": "19.99" (dollars, as text), "qty": int, "discount_pct": int}."""
from decimal import Decimal

from billing.money import dollars


def line_subtotal(line: dict) -> Decimal:
    """Exact, unrounded: price x quantity less the discount."""
    return dollars(line["price"]) * line["qty"] * (100 - line.get("discount_pct", 0)) / 100


def cart_subtotal(cart: list) -> Decimal:
    return sum((line_subtotal(line) for line in cart), Decimal(0))
