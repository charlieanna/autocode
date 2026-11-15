"""Recognize overdeclared engineering changes without authorizing protected changes.

Pure classification only. Reports and their original declarations remain retained;
the revision guard still compares every protected field against the previous body.
"""

from __future__ import annotations

import re


def own_assumption(before: dict, after: dict, item: str) -> bool:
    """An assumption the Planner proposed itself is engineering detail, not the person's text.

    The Plan Reviewer can ask for such an assumption to change (a live review found the draft's
    cwd-relative notes.json contradicted the user's next-to-CLI answer). The Planner then had to
    ask the person for a change to its own default, report repair never declared it correctly, and
    the run paused (a live planning revision, 2026-10-21). Only a row whose previous basis is agent_proposed, or a
    decision the person delegated to the Planner, qualifies; deleting or rewording text saved from the person's
    answer or feedback stays a person's decision.
    """
    for key in ("accepted_assumptions", "delegated_decisions"):
        previous = next(
            (
                candidate
                for candidate in before.get(key, [])
                if isinstance(candidate, dict) and candidate.get("text") == item
            ),
            None,
        )
        if previous is None:
            continue
        return previous.get("basis") in ("agent_proposed", "delegated") and all(
            not (isinstance(candidate, dict) and candidate.get("text") == item) for candidate in after.get(key, [])
        )
    return False


def engineering_delta(
    row: dict, before: dict, after: dict, proof_corrections: set[str], protected_lists: tuple[str, ...]
) -> bool:
    if row.get("basis") != "agent_proposed" or row.get("answer_id"):
        return False
    item = row.get("item")
    if not isinstance(item, str):
        return False
    old = {criterion["id"] for criterion in before.get("acceptance_criteria", [])}
    new = {criterion["id"] for criterion in after.get("acceptance_criteria", [])}
    # Exact protected identities always win, even when they happen to look like
    # a proposal field, a milestone ID or one of the recognized aliases.
    protected = old | set(before.get("permission_boundaries", []))
    protected.update(text for key in protected_lists for text in before.get(key, []))
    if item in protected:
        return False
    if row.get("change") in ("reworded", "removed") and own_assumption(before, after, item):
        return True
    if row.get("change") != "reworded":
        return False
    if any(item == cid + " verification_method" for cid in proof_corrections):
        return True
    if any(item == cid + " (new criterion added)" for cid in new - old):
        return True
    if item == "acceptance_criteria":
        return bool(new - old) and all(
            criterion in after["acceptance_criteria"] for criterion in before.get("acceptance_criteria", [])
        )
    if item in ("technical_approach", "milestones", "initial_task"):
        return item in after and before.get(item) != after[item]
    milestones = {row["id"]: row for row in before.get("milestones", [])}
    proposed = {row["id"]: row for row in after.get("milestones", [])}
    if item in milestones and item in proposed and milestones[item] != proposed[item]:
        return True
    return names_unprotected_addition(item, before, after, protected)


def _names(item: str, identity: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(identity) + r"(?![\w-])", item))


def names_unprotected_addition(item: str, before: dict, after: dict, protected: set) -> bool:
    """Whether a declared rewording names a criterion the revision added or a milestone it changed.

    The Planner declares such a change in its own words: ``AC9``, ``M1.acceptance_criteria``, ``milestone M1's
    acceptance_criteria (AC4, AC9)``. The labels above recognize a few spellings; any other was taken for a
    protected item that "needs a saved user answer", and a live run (2026-11-15) asked the person to authorize
    listing a new suite criterion under M1. What the row names is read from the two bodies instead: an ID that
    is new, or a milestone that differs. Such a row grants nothing, because the guard still compares every
    protected criterion, list and boundary itself; a row that quotes protected text stays the guard's to judge.
    """
    old = {row["id"]: row for row in before.get("acceptance_criteria", [])}
    added = [row["id"] for row in after.get("acceptance_criteria", []) if row.get("id") not in old]
    milestones = {row.get("id"): row for row in before.get("milestones", []) if isinstance(row, dict)}
    changed = [
        row["id"]
        for row in after.get("milestones", [])
        if isinstance(row, dict) and row.get("id") and milestones.get(row["id"]) != row
    ]
    quoted = {text for text in protected if text not in old} | {str(row.get("criterion") or "") for row in old.values()}
    if any(text and text in item for text in quoted):
        return False
    return any(_names(item, identity) for identity in (*added, *changed) if isinstance(identity, str) and identity)
