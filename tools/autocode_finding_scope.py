"""Decide which open findings can block acceptance of one approved milestone."""
from __future__ import annotations


def relevant_blockers(blockers, current, milestones):
    """Keep uncertain and prerequisite findings, and another milestone's findings recorded only
    against criteria the current milestone shares; defer the rest of other milestones' work."""
    by_id = {m.get("id"): m for m in milestones if isinstance(m, dict) and m.get("id")}
    current_ids = current.get("milestone_ids") or [current.get("id")]
    required = current.get("acceptance_criteria")
    if (not current_ids or not all(mid in by_id for mid in current_ids)
            or not isinstance(required, list) or not required):
        return list(blockers)
    current_criteria = set(required)
    if not current_criteria or any(not isinstance(cid, str) or not cid for cid in required):
        return list(blockers)

    # A finding against an ancestor may affect the current milestone even when
    # its recorded criterion does not overlap this milestone's own criteria.
    prerequisites = set()
    pending = list(current_ids)
    while pending:
        milestone = by_id[pending.pop()]
        for parent in milestone.get("depends_on", []):
            if parent not in by_id:
                return list(blockers)
            if parent not in prerequisites and parent not in current_ids:
                prerequisites.add(parent)
                pending.append(parent)

    def unrelated(row):
        saved = row.get("scope")
        if not isinstance(saved, dict):
            return False
        owner_id = saved.get("milestone_id")
        criteria = saved.get("criteria")
        if (owner_id not in by_id or owner_id in current_ids or owner_id in prerequisites
                or not isinstance(criteria, list) or not criteria
                or any(not isinstance(cid, str) or not cid for cid in criteria)):
            return False
        owner_criteria = by_id[owner_id].get("acceptance_criteria")
        if not isinstance(owner_criteria, list) or not owner_criteria:
            return False
        # A saved scope that does not match the owner's approved scope fails closed.
        recorded = set(criteria)
        if not recorded <= set(owner_criteria):
            return False
        # Otherwise it blocks here only when every criterion it was recorded against is
        # one of the current milestone's: only then can this milestone's reviewer close it
        # (autocode_findings._covers). A finding this reviewer may not close would block
        # forever; it stays open for its own milestone and for final completion instead.
        return not recorded <= current_criteria

    return [row for row in blockers if not unrelated(row)]
