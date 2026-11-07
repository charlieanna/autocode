"""Exact, bounded text corrections to complete malformed report JSON.

The model supplies every correction. No parsing heuristic invents structure;
ordinary report validation remains the caller's responsibility.
"""

from __future__ import annotations

import json
import re

try:
    from .autocode_report_bounds import MAX_REPORT_BYTES
except ImportError:
    from autocode_report_bounds import MAX_REPORT_BYTES

MAX_EDITS = 32
MAX_CHANGED_BYTES = 4096
RULE = (
    "BOUNDED JSON TEXT REPAIR. For malformed JSON the output schema offers repair_report.text_edits. "
    "Prefer this form instead of retranscribing the report: return only repair_report with the exact "
    "rejected_report.sha256 and text_edits, an array of {find, replace} objects. Each nonempty find must "
    "occur exactly once in the original rejected_report.content, with enough adjacent text to be unique. "
    "All edits refer to that original text, not an intermediate result; matches must not overlap. "
    "Supply at most 32 edits and 4096 UTF-8 bytes total across find/replace strings. Include missing "
    "wrappers or required fields explicitly; the runner never guesses them. Unedited text is retained "
    "exactly. A JSON fence or prose prefix is part of the pinned input and must be removed explicitly "
    "by your edits. The complete reconstructed object still undergoes every schema, provenance, evidence and "
    "protected-contract check. This grants no changed requirements, evidence or approval. A complete "
    "corrected report remains supported; instructions to return a complete report also permit this "
    "exact-bound text-edit form.\n"
)


def _object_shaped(text):
    """Offer edits for one JSON fence; never unwrap or repair its contents."""
    stripped = text.strip()
    if stripped.startswith("{"):
        return True
    match = re.fullmatch(r"([^`]*?)```json[ \t]*\r?\n(.*)\r?\n```[ \t]*", stripped, re.DOTALL)
    if not match or len(match[1].encode("utf-8")) > 1024 or "```" in match[2]:
        return False
    body = match[2].strip()
    return body.startswith("{") and body.endswith("}")


def eligible(source):
    text = source.get("content")
    if (
        source.get("format") != "text"
        or source.get("truncated")
        or not isinstance(text, str)
        or len(text.encode("utf-8")) > MAX_REPORT_BYTES
        or not _object_shaped(text)
    ):
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return True
    return False


def schema():
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": MAX_EDITS,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "find": {"type": "string", "minLength": 1, "maxLength": MAX_CHANGED_BYTES},
                "replace": {"type": "string", "maxLength": MAX_CHANGED_BYTES},
            },
            "required": ["find", "replace"],
        },
    }


def apply(text, edits):
    """Replace unique disjoint original spans and parse; never modify the source."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise ValueError("Text repair requires 1 to 32 edits")
    spans, changed_bytes = [], 0
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"find", "replace"}:
            raise ValueError("Text repair requires only find and replace")
        find, replacement = edit["find"], edit["replace"]
        if not isinstance(find, str) or not find or not isinstance(replacement, str) or find == replacement:
            raise ValueError("Text repair requires a nonempty match and a changed string replacement")
        changed_bytes += len(find.encode("utf-8")) + len(replacement.encode("utf-8"))
        if changed_bytes > MAX_CHANGED_BYTES:
            raise ValueError("Text repair exceeds the 4096-byte change bound")
        start = text.find(find)
        if start < 0 or text.find(find, start + 1) >= 0:
            raise ValueError("Text repair match must occur exactly once in the pinned source")
        spans.append((start, start + len(find), replacement))
    spans.sort()
    parts, cursor = [], 0
    for start, end, replacement in spans:
        if start < cursor:
            raise ValueError("Text repair matches overlap")
        parts.extend((text[cursor:start], replacement))
        cursor = end
    parts.append(text[cursor:])
    corrected = "".join(parts)
    if len(corrected.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Text repair result exceeds the report size bound")
    value = json.loads(corrected)
    if not isinstance(value, dict):
        raise ValueError("Text repair must reconstruct one complete report object")
    return value
