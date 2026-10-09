"""The nightly supplier feed."""


def bulk_import(store, rows) -> int:
    """rows: (sku, name, price_cents) tuples. Adds new products and overwrites existing ones."""
    records = {sku: {"name": name, "price_cents": price_cents} for sku, name, price_cents in rows}
    store.put_many(records)
    return len(records)
