"""Documentary source labels, distinct from the values a brief asks code to implement.

Only a leading Markdown file:line label is a location. Inline paths, file-scope
instructions and output strings remain literals. Source-record references are
unquoted prose references to an ID in the authenticated inventory, never an
arbitrary task-like value. Original source text and citations are not rewritten.
"""

from __future__ import annotations

import re

_LOCATION_LABEL = re.compile(
    r"^[ \t]*(?:[-*+]\s+)?`(?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w-]*:[1-9]\d*(?:[-–][1-9]\d*)?`:(?=\s)",
    re.M,
)
_INLINE_CODE = re.compile(r"(`[^`\n]*`)")
_OUTPUT = re.compile(r"\b(?:print|emit|output|return|literal|exactly)\b", re.I)


def without_location_labels(text: str) -> str:
    """Remove only documentary labels for the obligation scan, retaining their prose."""
    return _LOCATION_LABEL.sub(" ", text)


def value_text(text: str, source_ids) -> str:
    """Text whose numbers denote values, with known source-reference metadata removed."""
    text = without_location_labels(text)
    ids = "|".join(re.escape(value) for value in sorted(set(source_ids), key=len, reverse=True))
    if not ids:
        return text
    reference = re.compile(r"\b(?:original|from|source)\s+(?:" + ids + r")(?=$|[\s,.;:])")
    parts = _INLINE_CODE.split(text)
    for index in range(0, len(parts), 2):
        part = parts[index]
        parts[index] = reference.sub(
            lambda match: match.group() if _OUTPUT.search(part[: match.start()]) else " ", part
        )
    return "".join(parts)
