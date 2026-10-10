"""A small per-process cache with a time-to-live. Nothing here is visible to
other processes; each worker keeps its own copy."""

import time

TTL_SECONDS = 60


class LocalCache:
    def __init__(self, ttl_seconds: float = TTL_SECONDS, clock=time.monotonic):
        self.ttl = ttl_seconds
        self.clock = clock
        self._items: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        entry = self._items.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self.clock() - stored_at >= self.ttl:
            del self._items[key]
            return None
        return value

    def put(self, key: str, value) -> None:
        self._items[key] = (self.clock(), value)
