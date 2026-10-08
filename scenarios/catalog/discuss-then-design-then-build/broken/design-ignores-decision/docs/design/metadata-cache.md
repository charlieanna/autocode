# Metadata cache

Follows docs/decisions/metadata-cache.json, but keeps the cache in each worker: an in-process cache is
simpler and has no I/O on a hit, and a TTL fixes the stale reads.

## Decisions

Freshness is settled: TTL is caller-configurable through `ttl_seconds`, with a 3600-second default.
Use the injectable clock; expire at elapsed clock time >= TTL, including equality, and never serve stale metadata.
On expiration fetch metadata once and propagate upstream errors, never returning expired data.
The later build edits only app/ and tests/, preserving this design, the decision note and root README.md.
Use controlled urllib.request.urlopen responses and socket-free pipes or file barriers for process tests, not network listeners.

1. Module `app/ttl_cache.py`, class `TTLCache(ttl_seconds, clock)`, one per worker process.
2. One method reads through the cache: `get_or_fetch(key, fetch)`. An entry younger than `ttl_seconds` is
   returned without calling `fetch`.
3. `app/metadata.py` keeps `metadata(tld)` as the entry point and replaces `functools.lru_cache` with a
   module-level `TTLCache(3600, time.monotonic)`.

## Rejected

- A shared file cache: file locking and atomic writes are more moving parts than this needs.
