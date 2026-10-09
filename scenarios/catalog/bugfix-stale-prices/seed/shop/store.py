"""The product store: the one place product records live (a database in production)."""


class ProductStore:
    def __init__(self, products=None):
        self._rows = {sku: dict(row) for sku, row in (products or {}).items()}

    def get(self, sku: str) -> dict:
        """A copy of the product's record; KeyError for an unknown SKU."""
        return dict(self._rows[sku])

    def put(self, sku: str, row: dict) -> None:
        self._rows[sku] = dict(row)

    def put_many(self, rows: dict) -> None:
        for sku, row in rows.items():
            self._rows[sku] = dict(row)

    def skus(self) -> list:
        return sorted(self._rows)
