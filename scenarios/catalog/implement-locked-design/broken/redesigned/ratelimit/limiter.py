"""A simpler single-module rate limiter: one class owns all the buckets."""

from collections.abc import Callable


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self._tokens = float(capacity)
        self._last = clock()

    def _refill(self) -> None:
        now = self.clock()
        self._tokens = min(float(self.capacity), self._tokens + (now - self._last) * self.refill_per_second)
        self._last = now

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens

    def try_acquire(self, tokens: int = 1) -> bool:
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class RateLimiter:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]):
        self.capacity, self.refill_per_second, self.clock = capacity, refill_per_second, clock
        self._buckets: dict[str, TokenBucket] = {}

    def for_key(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(self.capacity, self.refill_per_second, self.clock)
        return self._buckets[key]

    def keys(self) -> list[str]:
        return sorted(self._buckets)
