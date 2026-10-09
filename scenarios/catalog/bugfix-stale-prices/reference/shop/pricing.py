"""Price changes from the admin page."""


def set_price(store, cache, sku: str, price_cents: int) -> None:
    """``cache`` is kept for callers; the store tells every cache about the change."""
    row = store.get(sku)
    row["price_cents"] = price_cents
    store.put(sku, row)
