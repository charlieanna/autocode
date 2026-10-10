"""Every checkout worker charges the current price after any write, through any path, and still caches."""

import unittest

from shop.cache import PriceCache
from shop.checkout import total
from shop.imports import bulk_import
from shop.pricing import set_price
from shop.promotions import apply_discount
from shop.store import ProductStore


class CurrentPriceEverywhere(unittest.TestCase):
    def setUp(self):
        self.store = ProductStore(
            {"A": {"name": "Apple", "price_cents": 1000}, "B": {"name": "Bread", "price_cents": 500}}
        )
        self.workers = [PriceCache(self.store), PriceCache(self.store), PriceCache(self.store)]
        for cache in self.workers:
            self.assertEqual(1500, total(cache, {"A": 1, "B": 1}))  # every worker has both prices cached

    def charged(self, sku):
        return [total(cache, {sku: 1}) for cache in self.workers]

    def test_admin_price_change_reaches_every_worker(self):
        set_price(self.store, self.workers[0], "A", 1200)
        self.assertEqual([1200, 1200, 1200], self.charged("A"))

    def test_supplier_import_reaches_every_worker(self):
        bulk_import(self.store, [("A", "Apple", 900), ("C", "Cheese", 700)])
        self.assertEqual([900, 900, 900], self.charged("A"))
        self.assertEqual([700, 700, 700], self.charged("C"))

    def test_discount_reaches_every_worker(self):
        apply_discount(self.store, ["A", "B"], 10)
        self.assertEqual([900, 900, 900], self.charged("A"))
        self.assertEqual([450, 450, 450], self.charged("B"))

    def test_a_future_write_path_needs_no_cache_code(self):
        # A write path added later that knows nothing about caches, like the planned ones.
        self.store.put("A", {"name": "Apple", "price_cents": 1111})
        self.assertEqual([1111, 1111, 1111], self.charged("A"))
        self.store.put_many({"B": {"name": "Bread", "price_cents": 555}})
        self.assertEqual([555, 555, 555], self.charged("B"))

    def test_repeated_changes(self):
        for price in (1300, 800, 1000):
            set_price(self.store, self.workers[1], "A", price)
            self.assertEqual([price] * 3, self.charged("A"))

    def test_a_worker_started_after_the_change(self):
        apply_discount(self.store, ["A"], 50)
        self.assertEqual(500, total(PriceCache(self.store), {"A": 1}))


class CheckoutStillCaches(unittest.TestCase):
    def test_unchanged_prices_are_not_reread(self):
        store = ProductStore({"A": {"name": "Apple", "price_cents": 1000}, "B": {"name": "Bread", "price_cents": 500}})
        reads = []
        original = store.get
        store.get = lambda sku: (reads.append(sku), original(sku))[1]
        cache = PriceCache(store)
        for _ in range(50):
            total(cache, {"A": 1, "B": 2})
        self.assertLessEqual(len(reads), 2, f"checkout read the store {len(reads)} times for 2 unchanged prices")
        set_price(store, cache, "A", 1200)
        before = len(reads)
        for _ in range(50):
            self.assertEqual(2200, total(cache, {"A": 1, "B": 2}))
        self.assertLessEqual(len(reads) - before, 2, "prices were not cached again after the change")


if __name__ == "__main__":
    unittest.main()
