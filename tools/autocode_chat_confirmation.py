"""Explicit, unqualified confirmations in the Requirements conversation."""

from __future__ import annotations

import re

AFFIRMATIONS = frozenset(
    (
        "y",
        "yes",
        "correct",
        "yes correct",
        "that's correct",
        "that is correct",
        "yes that's correct",
        "yes that is correct",
    )
)


def affirmative(reply: str) -> bool:
    """Recognize whole confirmation phrases while retaining qualifications as feedback."""
    text = re.sub(r"[,.!]", " ", reply.casefold().replace("’", "'"))
    return " ".join(text.split()) in AFFIRMATIONS
