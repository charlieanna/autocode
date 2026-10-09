"""A token bucket that reads time only from an injected clock."""
from collections.abc import Callable

from .errors import RateLimited


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

    def try_acquire(self, tokens: int = 1) -> None:
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return None
        raise RateLimited((tokens - self._tokens) / self.refill_per_second)
