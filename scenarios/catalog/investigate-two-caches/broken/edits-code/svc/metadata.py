"""Registry metadata as parsed Python objects (local cache removed)."""

import json

from . import cache_shared


def raw(tld: str) -> str:
    document = cache_shared.get(f"metadata-raw-{tld}")
    if document is None:
        raise LookupError(f"no metadata for {tld}; run the sync job")
    return document


def parsed(tld: str) -> dict:
    return json.loads(raw(tld))
