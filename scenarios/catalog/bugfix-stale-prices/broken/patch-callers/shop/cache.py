"""Per-worker price cache in front of the store."""

_ALL = []


def invalidate_everywhere(sku: str) -> None:
    for cache in _ALL:
        cache.invalidate(sku)


class PriceCache:
    def __init__(self, store):
        self.store = store
        self._prices = {}
        _ALL.append(self)

    def price(self, sku: str) -> int:
        if sku not in self._prices:
            self._prices[sku] = self.store.get(sku)["price_cents"]
        return self._prices[sku]

    def invalidate(self, sku: str) -> None:
        self._prices.pop(sku, None)
