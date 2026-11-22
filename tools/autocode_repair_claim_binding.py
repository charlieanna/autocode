"""Bind an accepted repair's concern-answer claims to its accepted change list (#3908).

A report repair may drop a ``contract_changes`` row the refused report declared — the revision
guard's instruction is "drop that row" when the protected item did not change. The repair's
concern responses and summary can still claim the row was re-emitted; acceptance applies the
report verbatim, so that claim reaches the person and the next review as a published change.

This pure helper marks such claims unverified against the accepted change list. It grants and
refuses nothing: the acceptance gates stay exactly as they were, and a repair that keeps every
declared row, or whose answers never mention a dropped row, is untouched.
"""

from __future__ import annotations

import copy
import re

MARKER = "[AutoCode:"
# The prose fields of a concern answer, and the field that carries the runner's note.
ANSWER_FIELDS = {"responses": ("response", "change"), "decisions": ("decision", "rationale")}
PRIMARY_FIELD = {"responses": "response", "decisions": "decision"}


def _declared_items(report):
    """The string items a report's contract_changes declares, or None when it has no such list."""
    if not isinstance(report, dict):
        return None
    rows = report.get("contract_changes")
    if not isinstance(rows, list):
        return None
    return {
        row["item"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("item"), str) and row["item"].strip()
    }


def _mentions(text, item):
    # The same word boundary canonical_items uses, so "AC1" does not match inside "AC12".
    return isinstance(text, str) and re.search(r"(?<![\w-])" + re.escape(item) + r"(?![\w-])", text) is not None


def _note(items):
    named = ", ".join(items)
    rows = "rows" if len(items) > 1 else "row"
    return (
        f" {MARKER} this repair removed the contract_changes {rows} for {named}; the accepted change list "
        f"declares no change for {named}, so a claim that this revision changed or re-emitted {named} "
        "is unverified.]"
    )


def bind(original_report, repaired_report):
    """Return ``(report, claims)`` for applying an accepted repair.

    ``claims`` names each concern answer (and the summary) that mentions a contract-change row
    the repair removed, with the note appended to its prose. It is empty — and the report is the
    same object, untouched — when either report declares no readable change list, the repair
    dropped no row, or no answer mentions a dropped row. The note is deterministic, so binding
    an already-annotated report again (revalidation reads the saved bytes) changes nothing.
    """
    before, after = _declared_items(original_report), _declared_items(repaired_report)
    if before is None or after is None:
        return repaired_report, []
    dropped = sorted(before - after)
    if not dropped or not isinstance(repaired_report, dict):
        return repaired_report, []

    # Plan the notes against the original bytes first; copy once, only if one is needed.
    planned = []
    for field, prose in ANSWER_FIELDS.items():
        rows = repaired_report.get(field)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            named = {item: [key for key in prose if _mentions(row.get(key), item)] for item in dropped}
            named = {item: keys for item, keys in named.items() if keys}
            primary = PRIMARY_FIELD[field]
            if named and isinstance(row.get(primary), str) and _note(sorted(named)) not in row[primary]:
                planned.append((field, index, named))
    summary = repaired_report.get("summary")
    summary_named = {item: ["summary"] for item in dropped if _mentions(summary, item)}
    if summary_named and isinstance(summary, str) and _note(sorted(summary_named)) not in summary:
        planned.append(("summary", None, summary_named))
    if not planned:
        return repaired_report, []

    result = copy.deepcopy(repaired_report)
    claims = []
    for field, index, named in planned:
        note = _note(sorted(named))
        if field == "summary":
            result["summary"] += note
        else:
            result[field][index][PRIMARY_FIELD[field]] += note
        concern_id = "" if field == "summary" else str(result[field][index].get("concern_id") or "")
        claims.extend({"item": item, "concern_id": concern_id, "where": keys} for item, keys in sorted(named.items()))
    return result, claims
