"""Per-worker price cache in front of the store."""


class PriceCache:
    def __init__(self, store):
        self.store = store

    def price(self, sku: str) -> int:
        return self.store.get(sku)["price_cents"]

    def invalidate(self, sku: str) -> None:
        pass
