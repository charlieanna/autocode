"""Registry metadata: fetched from the upstream registry API, parsed, and cached.

The upstream API allows **60 metadata requests per hour per account**; going
over returns 429 for the rest of the hour, and every request handler that needs
metadata then fails. A metadata document changes a few times a day at most.
"""

import functools
import json
import urllib.request

UPSTREAM = "https://registry.example.test/metadata/"


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


@functools.lru_cache(maxsize=256)
def metadata(tld: str) -> dict:
    """Parsed metadata for a TLD, cached in this process for its lifetime."""
    return fetch(tld)
