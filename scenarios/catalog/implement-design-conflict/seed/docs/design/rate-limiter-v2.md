# Rate limiter: explicit denials — approved design

Status: **approved** by the platform review on 2026-09-12. Implement as written.

## Change

`TokenBucket.try_acquire(tokens=1)` **raises `ratelimit.RateLimited`** when the
request cannot be satisfied, instead of returning `False`. `RateLimited`
carries `retry_after: float`, the seconds until enough tokens will be
available. On success `try_acquire` returns `None`.

Rationale: callers kept ignoring the boolean. An exception cannot be ignored,
and `retry_after` lets the API layer set a `Retry-After` header.

## Modules

- `ratelimit/bucket.py`: `TokenBucket` as today, with the new `try_acquire`
  contract and a `RateLimited` exception class defined in
  `ratelimit/errors.py`.
- `ratelimit/__init__.py` re-exports `TokenBucket` and `RateLimited`.
- `api/handler.py`: catch `RateLimited` and return `429` with the
  `retry_after` value in the body.

## Versioning

Ship as `ratelimit` **1.4**; no callers outside this repository are affected.
