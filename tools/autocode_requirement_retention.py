"""Keep authenticated requirement provenance across handoff refreshes.

An unchanged requirement may quote a smaller slice of its old source. Restore the
already validated full quote rather than losing provenance or inventing an omission.
Actual removals/replacements still need a saved user basis. Report all such errors
together so one repair does not discover them one at a time.
"""

from __future__ import annotations

import json

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


def check(previous, report, quote_by_id, saved_user_basis):
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
            errors.append(
                f"Refreshed handoff {action} requirement {rid} without a user-backed omission "
                f"in ignored_requirements; previous source_quote: {json.dumps(original, ensure_ascii=False)}"
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
