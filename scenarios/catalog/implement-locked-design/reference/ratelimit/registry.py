"""One token bucket per key, all sharing the registry's clock."""
from collections.abc import Callable

from .bucket import TokenBucket


class LimiterRegistry:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def for_key(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = TokenBucket(self.capacity, self.refill_per_second, self.clock)
        return bucket

    def keys(self) -> list[str]:
        return sorted(self._buckets)
