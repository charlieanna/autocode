"""Keep authenticated requirement provenance across handoff refreshes.

An unchanged requirement may quote a smaller slice of its old source. Restore the
already validated full quote rather than losing provenance or inventing an omission.
Actual removals/replacements still need a saved user basis. Report all such errors
together so one repair does not discover them one at a time.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "for",
        "from",
        "in",
        "is",
        "it",
        "its",
        "must",
        "no",
        "not",
        "of",
        "on",
        "or",
        "should",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)

try:
    from . import autocode_requirement_quotes as quotes
except ImportError:
    import autocode_requirement_quotes as quotes


def retained_quote(row, quote, previous, sources, *, replacement=False):
    """Return the old, still-grounded quote only for an identical retained requirement."""
    if replacement or not previous or row.get("text") != previous.get("text"):
        return quote
    original = quotes.ground(previous.get("source_quote"), sources)
    if original and quote in original:
        return original
    return quote


def _terms(text):
    terms = set()
    for word in _WORD.findall(str(text or "").casefold()):
        if word in _STOP_WORDS:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        if len(word) > 2:
            terms.add(word)
    return terms


def correction_sources(state: RunState, previous):
    """Map saved user corrections to prior requirements by shared substantive wording."""
    events = []
    user_events = state.get("user_events") or []
    for event in state.get("brief_feedback") or []:
        if isinstance(event, dict) and event.get("id") and event in user_events:
            events.append({"basis": "user_feedback", "event_id": event["id"], "text": event.get("text", "")})
    for event_id, answer in (state.get("answers") or {}).items():
        text = answer.get("text", "") if isinstance(answer, dict) else answer
        events.append({"basis": "user_answer", "event_id": event_id, "text": text})

    requirements = [
        (
            row["id"],
            _terms(row.get("text") or row.get("source_quote", "")),
        )
        for row in previous
        if isinstance(row, dict) and row.get("id")
    ]
    sources = []
    for event in events:
        text = event["text"]
        if not isinstance(text, str) or not text.strip():
            continue
        event_terms = _terms(text)
        candidate = {
            **event,
            "previous_requirement_ids": [
                requirement_id for requirement_id, terms in requirements if len(event_terms & terms) >= 2
            ],
        }
        sources.append(candidate)
    return sources


def check(previous, report, quote_by_id, saved_user_basis, correction_sources=()):
    """Reject every unbacked omission or quote replacement; never infer user authority."""
    if not previous:
        return
    ignored = report.get("ignored_requirements", [])
    if not isinstance(ignored, list):
        raise ValueError("ignored_requirements must be an array")
    by_id = {}
    for omission in ignored:
        if not isinstance(omission, dict):
            raise ValueError("ignored_requirements entries must be objects")
        rid = omission.get("requirement_id")
        if rid in by_id:
            raise ValueError(f"Duplicate ignored_requirements entry for {rid}")
        by_id[rid] = omission
    present = {row["id"] for row in report.get("requirements", [])}
    errors = []
    for previous_row in previous:
        rid = previous_row["id"]
        original = str(previous_row.get("source_quote", "")).strip()
        if original and quote_by_id.get(rid) == original:
            continue
        omission = by_id.get(rid)
        if not omission:
            action = "changed source_quote for" if rid in present else "dropped"
            candidates = [
                f"{source['basis']}:{source['event_id']}"
                for source in correction_sources
                if rid in source.get("previous_requirement_ids", [])
            ]
            errors.append(
                f"Refreshed handoff {action} requirement {rid} without a user-backed omission "
                f"in ignored_requirements; previous source_quote: {json.dumps(original, ensure_ascii=False)}"
                + (f"; candidate saved corrections: {', '.join(candidates)}" if candidates else "")
                + (
                    f"; new source_quote: {json.dumps(quote_by_id.get(rid), ensure_ascii=False)}"
                    if rid in present
                    else ""
                )
            )
        elif not str(omission.get("reason", "")).strip():
            errors.append(f"Omission of requirement {rid} needs a reason")
        elif not saved_user_basis(omission.get("basis"), omission.get("event_id")):
            errors.append(
                f"Omission of requirement {rid} needs a saved user answer or feedback event, "
                "not an agent-authored reason"
            )
    if errors:
        errors.append(
            "For every retained requirement, keep its existing id and previous source_quote. "
            "For a user-requested replacement or removal, record ignored_requirements with the saved "
            "user_answer/user_feedback basis and event_id; give a replacement a fresh requirement id."
        )
        raise ValueError("\n".join(errors))
