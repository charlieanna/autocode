"""Cart lines: {"sku": str, "price": "19.99" (dollars, as text), "qty": int, "discount_pct": int}."""


def line_subtotal(line: dict) -> float:
    return float(line["price"]) * line["qty"] * (1 - line.get("discount_pct", 0) / 100)


def cart_subtotal(cart: list) -> float:
    return sum(line_subtotal(line) for line in cart)
