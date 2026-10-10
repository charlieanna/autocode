"""A token bucket that reads time only from an injected clock."""

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
