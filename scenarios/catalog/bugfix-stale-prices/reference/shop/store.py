"""The product store: the one place product records live (a database in production).

Every write goes through ``put``/``put_many``, which tell each subscriber (every
worker's PriceCache) which SKUs changed, so no write path has to know about caches.
"""


class ProductStore:
    def __init__(self, products=None):
        self._rows = {sku: dict(row) for sku, row in (products or {}).items()}
        self._subscribers = []

    def subscribe(self, on_change) -> None:
        """Call ``on_change(sku)`` after every write to that SKU."""
        self._subscribers.append(on_change)

    def get(self, sku: str) -> dict:
        """A copy of the product's record; KeyError for an unknown SKU."""
        return dict(self._rows[sku])

    def put(self, sku: str, row: dict) -> None:
        self._rows[sku] = dict(row)
        self._changed([sku])

    def put_many(self, rows: dict) -> None:
        for sku, row in rows.items():
            self._rows[sku] = dict(row)
        self._changed(rows)

    def skus(self) -> list:
        return sorted(self._rows)

    def _changed(self, skus) -> None:
        for sku in skus:
            for on_change in self._subscribers:
                on_change(sku)
