"""Percentage discounts on a set of products."""
from shop.cache import invalidate_everywhere


def apply_discount(store, skus, percent: int) -> None:
    for sku in skus:
        row = store.get(sku)
        row["price_cents"] = round(row["price_cents"] * (100 - percent) / 100)
        store.put(sku, row)
        invalidate_everywhere(sku)
