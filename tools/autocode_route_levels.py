"""Read model metadata from the tools' local model listings, without model calls.

``opencode models --verbose`` and ``kilo models --verbose`` print each model id followed by its
JSON metadata object (models.dev's schema). ``parse`` keeps the reasoning variants; ``metadata``
also keeps the facts automatic selection checks routes by (#2049): the model ``family``
(verifier independence), whether it calls tools or reasons, its context window and release date.
They are facts, not a ranking: how capable a model is comes from AutoCode's own measurements,
never from a listing, and a list price is deliberately not read (owner decision 2026-10-30).
Every other field is ignored, and a field the listing does not carry is None: unknown, never
guessed.
"""

from __future__ import annotations

import json


def _entries(listing):
    """Yield (model id, metadata dict) for each model the listing describes with a JSON object."""
    decoder = json.JSONDecoder()
    remaining = listing.strip()
    while remaining:
        model, separator, remaining = remaining.partition("\n")
        remaining = remaining.lstrip()
        if not separator:
            break
        if not remaining.startswith("{"):
            continue
        try:
            found, end = decoder.raw_decode(remaining)
        except ValueError:
            break
        remaining = remaining[end:].lstrip()
        if "/" in model and isinstance(found, dict):
            yield model.strip(), found


def parse(listing):
    """The reasoning variants each model advertises.

    Missing variants mean unknown, while an explicit empty object means no levels.
    Ignore all other metadata (including headers), and retain only variant names.
    """
    return {
        model: sorted(found["variants"])
        for model, found in _entries(listing)
        if isinstance(found.get("variants"), dict)
    }


def _flag(capabilities, name):
    value = capabilities.get(name) if isinstance(capabilities, dict) else None
    return value if isinstance(value, bool) else None


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def metadata(listing):
    """Per model: ``family``, ``toolcall``, ``reasoning``, ``context`` and ``release_date``;
    each None when the listing does not carry it."""
    found = {}
    for model, entry in _entries(listing):
        capabilities = entry.get("capabilities")
        limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else {}
        context = limit.get("context")
        found[model] = {
            "family": _text(entry.get("family")),
            "toolcall": _flag(capabilities, "toolcall"),
            "reasoning": _flag(capabilities, "reasoning"),
            "context": context if type(context) is int and context > 0 else None,
            "release_date": _text(entry.get("release_date")),
        }
    return found
