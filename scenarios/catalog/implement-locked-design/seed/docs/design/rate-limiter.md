# Rate limiter — approved design (v1)

Status: **approved**. Implement as written. Deviations need a new revision.

## Modules

`ratelimit/bucket.py`

```python
class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]): ...
    def try_acquire(self, tokens: int = 1) -> bool: ...
    @property
    def available(self) -> float: ...  # tokens right now, after refill, never above capacity
```

- A new bucket starts full.
- `try_acquire` refills by `(now - last) * refill_per_second`, capped at
  `capacity`, then takes `tokens` if available and returns True; otherwise it
  takes nothing and returns False. It never blocks and never sleeps.
- `clock` is the only source of time. **`bucket.py` must not import `time`.**
  Tests inject a fake clock.

`ratelimit/registry.py`

```python
class LimiterRegistry:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]): ...
    def for_key(self, key: str) -> TokenBucket: ...  # one bucket per key, created full on first use
    def keys(self) -> list[str]: ...  # keys seen so far, sorted
```

- `registry.py` must not import `time` either; it passes its clock to buckets.

`ratelimit/__init__.py` re-exports `TokenBucket` and `LimiterRegistry`.

## Constraints

- Standard library only. No threads, no module-level state, and no decorators other than the
  `@property` on `available`.
- Floats for time and tokens; `available` may be fractional.

## Rejected alternatives (do not revisit)

- One `RateLimiter` class holding a dict of buckets: rejected, because callers
  need a bucket object they can hold and inspect.
- Reading `time.monotonic()` inside the bucket: rejected, untestable.
