"""Per-worker price cache in front of the store, so a busy checkout does not read the store for every line."""


class PriceCache:
    def __init__(self, store):
        self.store = store
        self._prices = {}

    def price(self, sku: str) -> int:
        if sku not in self._prices:
            self._prices[sku] = self.store.get(sku)["price_cents"]
        return self._prices[sku]

    def invalidate(self, sku: str) -> None:
        self._prices.pop(sku, None)
