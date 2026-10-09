"""Registry metadata as parsed Python objects.

The raw metadata document is a few hundred kilobytes of JSON per TLD and
parsing it dominates request latency (about 40 ms). Parsed objects cannot be
shared between processes, so each worker keeps its own for a short time."""
import json

from . import cache_shared
from .cache_local import LocalCache

_parsed = LocalCache()


def raw(tld: str) -> str:
    document = cache_shared.get(f"metadata-raw-{tld}")
    if document is None:
        raise LookupError(f"no metadata for {tld}; run the sync job")
    return document


def parsed(tld: str) -> dict:
    """Parsed metadata for a TLD, re-parsed at most once a minute per worker."""
    value = _parsed.get(tld)
    if value is None:
        value = json.loads(raw(tld))
        _parsed.put(tld, value)
    return value
