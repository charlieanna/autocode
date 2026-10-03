# Bounded TTL and LRU Cache

`app.Cache(capacity, clock)` stores a bounded set of values using a caller-supplied
monotonic clock. `capacity` must be a positive integer and `clock` must return numeric
seconds.

```python
from app import Cache

clock = lambda: 0
cache = Cache(2, clock)
cache.put("key", "value", 5)
value = cache.get("key", default=None)
size = len(cache)
```

`put(key, value, ttl)` stores or replaces a value with a positive finite TTL. `get(key,
default=None)` returns a live value and updates its LRU recency, or returns `default` for
missing and expired keys. `len(cache)` reports live entries without changing recency.

Run the tests with:

```sh
python3 -m unittest discover -s tests -t .
```
