# ratelimit 1.x

Token-bucket rate limiting used by the API layer.

**Compatibility.** `ratelimit` is a published package on the 1.x line. Its
public API is frozen: `TokenBucket.try_acquire(tokens=1)` returns a `bool` and
never raises for a denied request. Callers in this repository and in three
other services depend on that. A change to the return contract is a 2.0 and
needs a migration plan for every caller.

    python3 -m unittest discover -s tests -t .
