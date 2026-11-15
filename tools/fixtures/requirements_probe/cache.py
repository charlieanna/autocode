"""A small in-memory cache for computed reports."""

import time

TTL_SECONDS = 3600  # entries older than an hour are recomputed

_entries = {}


def get(key, compute, now=time.time):
    stored = _entries.get(key)
    if stored is not None and now() - stored[0] < TTL_SECONDS:
        return stored[1]
    value = compute()
    _entries[key] = (now(), value)
    return value
