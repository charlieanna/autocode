# shop

The pricing core of a small web shop.

- `shop/store.py`: `ProductStore`, the one place product records live. It is
  slow in production (a database), so checkout does not read it for every line.
- `shop/cache.py`: `PriceCache(store)`, the per-worker price cache checkout reads
  through. Each checkout worker has its own `PriceCache` over the shared store.
- Ways a price changes today: `shop/pricing.py` (`set_price`, the admin page),
  `shop/imports.py` (`bulk_import`, the nightly supplier feed) and
  `shop/promotions.py` (`apply_discount`). More are planned.
- `shop/checkout.py`: `total(cache, cart)`, what the customer is charged.

Prices are integer cents.

## Tests

    python3 -m unittest discover -s tests -t .
