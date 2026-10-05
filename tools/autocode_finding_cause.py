"""Root-cause identity behind finding resolution inheritance (issue #300).

A finding settled by an explicit disposition or by the user's permission answer
settles its duplicates: open blocking rows sharing its cause -- the evidence path
where it was seen, the reporter's failure signature, and the normalized finding
text -- are marked resolved by inheritance from the settled row's id, recording
which finding's resolution closed them. The identity of a row itself stays
runner-owned in autocode_findings; this module only groups already-identified
rows.

The validate-only bookkeeping pause reuses the same identity so an empty recheck
cannot be dispatched twice in a row for a row another empty recheck already
produced at an unchanged source revision.

This module imports nothing from AutoCode: it operates on ledger rows and the
plain run-state dict in place.
"""
from __future__ import annotations


def _norm_text(text) -> str:
    """Case- and whitespace-insensitive finding wording, so equivalents group."""
    return " ".join(str(text).split()).casefold().rstrip(".!?").strip()


def cause_key(row) -> tuple:
    """(evidence path, failure signature, normalized finding text): a row's root cause."""
    return (str(row.get("evidence") or "").strip(),
            str(row.get("failure_signature") or "").strip().casefold(),
            _norm_text(row.get("finding") or ""))


def same_cause(left, right) -> bool:
    return cause_key(left) == cause_key(right)


def inherit_resolution(rows, resolved_id, *, resolved_in, evidence, at):
    """Mark open blocking rows with resolved_id's cause resolved by inheritance.

    Each closed row records the id whose resolution closed it (``inherited_from``)
    and cites that resolution in its resolution evidence. Returns the closed ids."""
    source = next((row for row in rows if row.get("id") == resolved_id), None)
    if source is None or source.get("status") == "open":
        return []
    key = cause_key(source)
    closed = []
    for row in rows:
        if (row is source or row.get("status") != "open" or not row.get("blocking", True)
                or cause_key(row) != key):
            continue
        row.update(status="resolved", resolved_at=at, resolved_in=resolved_in,
                   resolution_evidence=f"Same root cause as {resolved_id}: inherited from "
                                       f"{resolved_id}'s resolution ({evidence})",
                   inherited_from=resolved_id)
        row.pop("pending_resolution", None)
        row.pop("not_rechecked_in", None)
        closed.append(row["id"])
    return closed


def resolve_named(state, finding_ids, event):
    """The findings-side resolver for a user's permission answer naming finding ids.

    Each named open row is resolved citing the permission_answer event; same-cause
    open blocking rows then inherit that resolution. Returns every id closed."""
    rows = state.get("findings_ledger") or []
    at = event.get("at")
    cited = f"permission_answer:{event.get('question_id', '')}"
    evidence = f"Resolved by the user's permission answer {event.get('question_id', '')}: {event.get('text', '')}"
    closed = []
    for fid in finding_ids:
        row = next((r for r in rows if r.get("id") == fid and r.get("status") == "open"), None)
        if row is None:
            continue
        row.update(status="resolved", resolved_at=at, resolved_in=cited, resolution_evidence=evidence)
        row.pop("pending_resolution", None)
        row.pop("not_rechecked_in", None)
        closed.append(fid)
        closed.extend(inherit_resolution(rows, fid, resolved_in=cited, evidence=evidence, at=at))
    return closed
