"""HTTP-ish request handling with per-client rate limiting."""

from ratelimit import TokenBucket


class Handler:
    def __init__(self, clock, capacity=10, refill_per_second=1.0):
        self.clock = clock
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.buckets: dict[str, TokenBucket] = {}

    def handle(self, client: str, request: str) -> tuple[int, str]:
        bucket = self.buckets.setdefault(client, TokenBucket(self.capacity, self.refill_per_second, self.clock))
        if not bucket.try_acquire():
            return 429, "slow down"
        return 200, f"ok: {request}"
