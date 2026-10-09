import unittest

from shop.cache import PriceCache
from shop.checkout import total
from shop.imports import bulk_import
from shop.pricing import set_price
from shop.promotions import apply_discount
from shop.store import ProductStore


def products():
    return {"A": {"name": "Apple", "price_cents": 1000}, "B": {"name": "Bread", "price_cents": 500}}


class ShopTests(unittest.TestCase):
    def test_total(self):
        cache = PriceCache(ProductStore(products()))
        self.assertEqual(2500, total(cache, {"A": 2, "B": 1}))

    def test_set_price(self):
        store = ProductStore(products())
        cache = PriceCache(store)
        total(cache, {"A": 1})
        set_price(store, cache, "A", 1200)
        self.assertEqual(1200, total(cache, {"A": 1}))

    def test_bulk_import_writes_the_store(self):
        store = ProductStore(products())
        self.assertEqual(2, bulk_import(store, [("A", "Apple", 900), ("C", "Cheese", 700)]))
        self.assertEqual(900, store.get("A")["price_cents"])
        self.assertEqual(["A", "B", "C"], store.skus())

    def test_discount_rounds_to_cents(self):
        store = ProductStore(products())
        apply_discount(store, ["A", "B"], 15)
        self.assertEqual((850, 425), (store.get("A")["price_cents"], store.get("B")["price_cents"]))

    def test_checkout_reads_the_store_once_per_sku(self):
        store = ProductStore(products())
        reads = []
        original = store.get
        store.get = lambda sku: (reads.append(sku), original(sku))[1]
        cache = PriceCache(store)
        for _ in range(5):
            total(cache, {"A": 1, "B": 1})
        self.assertEqual(["A", "B"], sorted(reads))


if __name__ == "__main__":
    unittest.main()
