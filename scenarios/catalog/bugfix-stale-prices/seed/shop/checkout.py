"""What the customer is charged."""


def total(cache, cart: dict) -> int:
    """cart: {sku: quantity}. Returns the total in cents."""
    return sum(cache.price(sku) * quantity for sku, quantity in cart.items())
