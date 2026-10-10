"""Price changes from the admin page."""

from shop.cache import invalidate_everywhere


def set_price(store, cache, sku: str, price_cents: int) -> None:
    row = store.get(sku)
    row["price_cents"] = price_cents
    store.put(sku, row)
    invalidate_everywhere(sku)
