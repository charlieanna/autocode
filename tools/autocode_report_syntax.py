"""Remove bounded syntax defects from complete reports without changing any JSON value.

Two defects are corrected: a trailing comma before a closing delimiter, and a duplicated
object separator (``},","status"`` or ``},""status"`` where ``},"status"`` was meant).

This is syntax normalization, never report acceptance. Missing values, separators,
wrappers and closing delimiters remain model repairs. The caller preserves the
exact response and runs every ordinary schema, provenance and evidence check.
"""

from __future__ import annotations

import json
import re

try:
    from .autocode_report_bounds import MAX_REPORT_BYTES
except ImportError:
    from autocode_report_bounds import MAX_REPORT_BYTES

MAX_COMMAS = 32
MAX_STRAY = 4
TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|(?P<comma>,)(?=[ \t\r\n]*[}\]])', re.DOTALL)
# A duplicated separator between a nested object's close and the key that follows it:
# `},"status"` written as `},","status"` or `},""status"`. Only the stray text between
# the two groups is removed, so no key, value or structural delimiter changes.
STRAY_SEPARATOR = re.compile(r'(\}\s*,\s*)"(?:\s*,)?\s*(")')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate report key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("Nonfinite report number")


def trailing_commas(text: str) -> tuple[dict, list[int]] | None:
    """Return one complete object and removed Unicode offsets, or decline the correction."""
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        return None
    offsets: list[int] = []
    for match in TOKENS.finditer(text):
        if match.group("comma") is None:
            continue
        before = text[: match.start()].rstrip()
        if not before or before[-1] in "{[:," or len(offsets) == MAX_COMMAS:
            return None
        offsets.append(match.start())
    if not offsets:
        return None
    parts, start = [], 0
    for offset in offsets:
        parts.append(text[start:offset])
        start = offset + 1
    corrected = "".join([*parts, text[start:]])
    try:
        value = json.loads(corrected, object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (ValueError, RecursionError):
        return None
    return (value, offsets) if isinstance(value, dict) else None


def stray_object_separators(text: str) -> tuple[dict, list[int]] | None:
    """Return one complete object and the removed Unicode offsets, or decline the correction.

    No string mask decides what is editable: the defect desyncs any quote pairing, and a
    string value cannot contain this shape unescaped anyway. The bound and the requirement
    that the correction yield exactly one object are what keep it narrow; a correction that
    changed meaning still fails the caller's ordinary schema and evidence checks.
    """
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        return None
    offsets: list[int] = []
    parts, start = [], 0
    for index, match in enumerate(STRAY_SEPARATOR.finditer(text)):
        if index == MAX_STRAY:
            return None
        first, last = match.end(1), match.start(2)
        parts.append(text[start:first])
        offsets.extend(range(first, last))
        start = last
    if not offsets:
        return None
    parts.append(text[start:])
    try:
        value = json.loads("".join(parts), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (ValueError, RecursionError):
        return None
    return (value, offsets) if isinstance(value, dict) else None
