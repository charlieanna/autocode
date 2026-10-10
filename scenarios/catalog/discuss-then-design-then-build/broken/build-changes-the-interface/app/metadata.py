"""Registry metadata: fetched from the upstream registry API, parsed, and cached.

The upstream API allows **60 metadata requests per hour per account**; going
over returns 429 for the rest of the hour, and every request handler that needs
metadata then fails. A metadata document changes a few times a day at most.
The cache is shared by every worker on the host (docs/design/metadata-cache.md).
"""

import json
import os
import time
import urllib.request

from .shared_cache import SharedFileCache

UPSTREAM = "https://registry.example.test/metadata/"
CACHE = SharedFileCache(os.environ.get("METADATA_CACHE_DIR", "/var/cache/metadata-api"), 3600, time.time)


def fetch(tld: str) -> dict:
    with urllib.request.urlopen(UPSTREAM + tld, timeout=10) as response:
        return parse(response.read())


def parse(raw: bytes) -> dict:
    document = json.loads(raw)
    return {
        "tld": document["tld"],
        "grace_days": int(document["grace"]["days"]),
        "max_years": int(document["limits"]["renew_years"]),
    }


def metadata(tld: str) -> dict:
    """Parsed metadata for a TLD, from the host's shared cache."""
    return CACHE.get_or_fetch(tld, lambda: fetch(tld))
