"""A bounded TTL cache with least-recently-used eviction."""

import math
from collections import OrderedDict
from fractions import Fraction


class Cache:
    """Store values until their deadline, evicting least-recently-used live entries."""

    def __init__(self, capacity, clock):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")

        self._capacity = capacity
        self._clock = clock
        self._entries = OrderedDict()

    def put(self, key, value, ttl):
        """Store *value* under *key* with a fresh positive, finite TTL."""
        self._validate_ttl(ttl)
        now = self._clock()
        deadline = self._deadline(now, ttl)
        self._discard_expired(now)

        self._entries.pop(key, None)
        if len(self._entries) >= self._capacity:
            self._entries.popitem(last=False)
        self._entries[key] = (value, deadline)

    def get(self, key, default=None):
        """Return a live value and mark it most recently used, or *default*."""
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None:
            return default

        value, deadline = entry
        if now >= deadline:
            del self._entries[key]
            return default

        self._entries.move_to_end(key)
        return value

    def __len__(self):
        """Return the number of live entries without changing their recency."""
        self._discard_expired(self._clock())
        return len(self._entries)

    @staticmethod
    def _validate_ttl(ttl):
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            raise ValueError("ttl must be a positive finite integer or float")
        if isinstance(ttl, float) and not math.isfinite(ttl):
            raise ValueError("ttl must be a positive finite integer or float")
        if ttl <= 0:
            raise ValueError("ttl must be a positive finite integer or float")

    def _discard_expired(self, now):
        expired_keys = [key for key, (_, deadline) in self._entries.items() if now >= deadline]
        for key in expired_keys:
            del self._entries[key]

    @staticmethod
    def _deadline(now, ttl):
        try:
            deadline = now + ttl
        except OverflowError:
            return Fraction(now) + Fraction(ttl)
        if isinstance(deadline, float) and math.isinf(deadline) and math.isfinite(now):
            return Fraction(now) + Fraction(ttl)
        return deadline
