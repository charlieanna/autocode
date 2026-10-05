"""An in-process cache with a TTL: one per worker, as the design says."""


class TTLCache:
    def __init__(self, ttl_seconds, clock):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.entries = {}

    def get_or_fetch(self, key, fetch):
        entry = self.entries.get(key)
        if entry and self.clock() - entry[0] < self.ttl_seconds:
            return entry[1]
        value = fetch()
        self.entries[key] = (self.clock(), value)
        return value
