# shop

Order bookkeeping for a small online store.

## Time

Every timestamp in this package is Unix seconds (UTC). The store runs on its
own calendar: a store day is a day at UTC-8, all year round, with no daylight
saving (`STORE_UTC_OFFSET_HOURS` in `shop/clock.py`). "Delivered on March 1"
means March 1 in store time.

## Tests

    python3 -m unittest discover -s tests -t .
