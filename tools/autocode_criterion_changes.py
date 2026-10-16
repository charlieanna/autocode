"""Saved code checkpoints attributed to the approved plan's acceptance criteria (#730).

Attribution, not proof. Each code checkpoint row (``autocode_code_checkpoints``) names the
milestone whose task the Builder step served and the files it changed. Each criterion belongs
to the plan milestones that list it, so it is attributed every saved step of those milestones.
That tells a reviewer where to look, not that those changes satisfy the criterion. A file that a
step outside the criterion's milestones also changed is marked ``overlapping``, because that
file's diff may hold another milestone's work. A step whose milestone is not in the plan
(``None`` or unknown) is listed as unattributed.

Pure over the approved plan body and the saved rows; the status view shows the result as
``changes_by_criterion``, and ``autocode review`` reads the per-step diffs from it.
"""

from __future__ import annotations

MEANING = "Saved Builder steps of each criterion's milestones: where to look, not proof that these changes satisfy it."


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _ids(value) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def attribute(body, rows) -> dict | None:
    """Map the approved plan's criteria onto saved checkpoint rows; None without a plan body."""
    if not isinstance(body, dict) or not isinstance(body.get("acceptance_criteria"), list):
        return None
    milestones = {
        row["id"]: _ids(row.get("acceptance_criteria"))
        for row in _rows(body.get("milestones"))
        if isinstance(row.get("id"), str)
    }
    steps = [
        (row["id"], row.get("milestone_id"), set(_ids(row.get("changed_files"))))
        for row in _rows(rows)
        if isinstance(row.get("id"), str) and row["id"]
    ]
    criteria = []
    for criterion in _rows(body["acceptance_criteria"]):
        cid = criterion.get("id")
        if not isinstance(cid, str):
            continue
        owners = [mid for mid, covered in milestones.items() if cid in covered]
        own = [(ident, files) for ident, milestone, files in steps if milestone in owners]
        files = set().union(*(paths for _, paths in own))
        others = set().union(*(paths for _, milestone, paths in steps if milestone not in owners))
        criteria.append(
            {
                "id": cid,
                "milestones": owners,
                "checkpoints": [ident for ident, _ in own],
                "files": sorted(files),
                "overlapping": bool(files & others),
            }
        )
    return {
        "meaning": MEANING,
        "criteria": criteria,
        "unattributed": [ident for ident, milestone, _ in steps if milestone not in milestones],
    }
