"""Price changes from the admin page."""


def set_price(store, cache, sku: str, price_cents: int) -> None:
    row = store.get(sku)
    row["price_cents"] = price_cents
    store.put(sku, row)
    cache.invalidate(sku)
