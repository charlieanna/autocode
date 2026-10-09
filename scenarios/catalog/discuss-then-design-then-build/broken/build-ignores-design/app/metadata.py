"""Registry metadata: fetched from the upstream registry API, parsed, and cached.

The upstream API allows **60 metadata requests per hour per account**; going
over returns 429 for the rest of the hour, and every request handler that needs
metadata then fails. A metadata document changes a few times a day at most.
"""

import json
import os
import urllib.request

from .disk_cache import DiskCache

UPSTREAM = "https://registry.example.test/metadata/"
_CACHE = DiskCache(os.environ.get("METADATA_CACHE_DIR", "/var/cache/metadata-api"))


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


def lookup_metadata(tld: str) -> dict:
    return _CACHE.lookup(tld, lambda: fetch(tld))


metadata = lookup_metadata
