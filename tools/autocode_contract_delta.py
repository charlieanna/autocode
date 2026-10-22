"""Recognize overdeclared engineering changes without authorizing protected changes.

Pure classification only. Reports and their original declarations remain retained;
the revision guard still compares every protected field against the previous body.
"""

from __future__ import annotations


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
    return item in milestones and item in proposed and milestones[item] != proposed[item]
