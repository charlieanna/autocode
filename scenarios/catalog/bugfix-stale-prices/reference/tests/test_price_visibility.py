"""Regression tests for docs/bugs/stale-prices.json: checkout charged stale prices after an import or a discount."""

import unittest

from shop.cache import PriceCache
from shop.checkout import total
from shop.imports import bulk_import
from shop.pricing import set_price
from shop.promotions import apply_discount
from shop.store import ProductStore


class PriceVisibility(unittest.TestCase):
    def setUp(self):
        self.store = ProductStore({"A": {"name": "Apple", "price_cents": 1000}})
        self.workers = [PriceCache(self.store), PriceCache(self.store)]
        for cache in self.workers:
            total(cache, {"A": 1})

    def charged(self):
        return [total(cache, {"A": 1}) for cache in self.workers]

    def test_import_reaches_checkout(self):
        bulk_import(self.store, [("A", "Apple", 900)])
        self.assertEqual([900, 900], self.charged())

    def test_discount_reaches_checkout(self):
        apply_discount(self.store, ["A"], 20)
        self.assertEqual([800, 800], self.charged())

    def test_admin_change_reaches_other_workers(self):
        set_price(self.store, self.workers[0], "A", 1200)
        self.assertEqual([1200, 1200], self.charged())

    def test_any_store_write_reaches_checkout(self):
        self.store.put("A", {"name": "Apple", "price_cents": 1111})
        self.assertEqual([1111, 1111], self.charged())


if __name__ == "__main__":
    unittest.main()
