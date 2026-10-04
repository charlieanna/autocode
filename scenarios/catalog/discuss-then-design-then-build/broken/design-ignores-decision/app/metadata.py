"""Registry metadata: fetched from the upstream registry API, parsed, and cached.

The upstream API allows **60 metadata requests per hour per account**; going
over returns 429 for the rest of the hour, and every request handler that needs
metadata then fails. A metadata document changes a few times a day at most.
"""
import json
import time
import urllib.request

from .ttl_cache import TTLCache

UPSTREAM = "https://registry.example.test/metadata/"
CACHE = TTLCache(3600, time.monotonic)


def fetch(tld: str) -> dict:
    with urllib.request.urlopen(UPSTREAM + tld, timeout=10) as response:
        return parse(response.read())


def parse(raw: bytes) -> dict:
    document = json.loads(raw)
    return {"tld": document["tld"], "grace_days": int(document["grace"]["days"]),
            "max_years": int(document["limits"]["renew_years"])}


def metadata(tld: str) -> dict:
    return CACHE.get_or_fetch(tld, lambda: fetch(tld))
