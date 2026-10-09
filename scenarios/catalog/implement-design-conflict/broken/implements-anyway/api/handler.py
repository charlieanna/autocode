"""HTTP-ish request handling with per-client rate limiting."""
from ratelimit import RateLimited, TokenBucket


class Handler:
    def __init__(self, clock, capacity=10, refill_per_second=1.0):
        self.clock = clock
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.buckets: dict[str, TokenBucket] = {}

    def handle(self, client: str, request: str) -> tuple[int, str]:
        bucket = self.buckets.setdefault(client, TokenBucket(self.capacity, self.refill_per_second, self.clock))
        try:
            bucket.try_acquire()
        except RateLimited as denied:
            return 429, f"retry after {denied.retry_after:.2f}"
        return 200, f"ok: {request}"
