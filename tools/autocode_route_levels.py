"""Read reasoning variants from the tools' local model metadata, without model calls."""

from __future__ import annotations

import json


def parse(listing):
    """OpenCode and Kilo print a model id followed by its JSON metadata object.

    Missing variants mean unknown, while an explicit empty object means no levels.
    Ignore all other metadata (including headers), and retain only variant names.
    """
    decoder = json.JSONDecoder()
    levels = {}
    remaining = listing.strip()
    while remaining:
        model, separator, remaining = remaining.partition("\n")
        remaining = remaining.lstrip()
        if not separator:
            break
        if not remaining.startswith("{"):
            continue
        try:
            metadata, end = decoder.raw_decode(remaining)
        except ValueError:
            break
        remaining = remaining[end:].lstrip()
        variants = metadata.get("variants") if isinstance(metadata, dict) else None
        if "/" in model and isinstance(variants, dict):
            levels[model.strip()] = sorted(variants)
    return levels
