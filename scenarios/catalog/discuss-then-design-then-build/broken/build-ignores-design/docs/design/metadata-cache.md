# Shared metadata cache

Follows docs/decisions/metadata-cache.json (recommendation: shared-file): four gunicorn workers that share
nothing, recycled every ~2000 requests, against an upstream limit of 60 metadata requests per hour.

## Decisions

Freshness is settled: TTL is caller-configurable through `ttl_seconds`, with a 3600-second default.
Use the injectable clock; expire at elapsed clock time >= TTL, including equality, and never serve stale metadata.
On expiration fetch metadata once, publish atomically, and let other workers reuse the record; propagate upstream errors.
The later build edits only app/ and tests/, preserving this design, the decision note and root README.md.
Use controlled urllib.request.urlopen responses and socket-free pipes or file barriers for process tests, not network listeners.

1. Module `app/shared_cache.py`, class `SharedFileCache(directory, ttl_seconds, clock)`. `clock` is a
   zero-argument callable returning seconds; it is the cache's only source of time.
2. One method reads through the cache: `get_or_fetch(key, fetch)`. An entry younger than `ttl_seconds` is
   returned without calling `fetch`; otherwise `fetch()` runs and its result is stored.
3. One JSON file per key, `<directory>/<key>.json`, holding `{"fetched_at": <seconds>, "value": <document>}`.
   A write goes to a temporary file in the same directory and is moved into place with `os.replace`, so a
   reader never sees a partial file.
4. A miss takes an exclusive `fcntl.flock` on `<directory>/<key>.lock` and reads again before fetching, so
   workers that miss together fetch once.
5. The directory is the `METADATA_CACHE_DIR` the deploy configuration sets (deploy/gunicorn.conf.py),
   created if missing. TTL is 3600 seconds: metadata changes a few times a day at most.
6. `app/metadata.py` keeps `metadata(tld)` as the entry point and reads through a module-level
   `CACHE = SharedFileCache(os.environ.get("METADATA_CACHE_DIR", "/var/cache/metadata-api"), 3600, time.time)`.
   The per-process `functools.lru_cache` is removed.

## Rejected

- Keeping `functools.lru_cache` in each worker: four cold caches, refilled on every recycle.
- A cache server (Redis, memcached): a new service and dependency for one host.
