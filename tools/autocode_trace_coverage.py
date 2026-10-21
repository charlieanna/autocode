"""Requirement coverage and conflict-resolution errors for one plan, with no controller dependencies."""

from __future__ import annotations

import re


def coverage_errors(requirements, by_id, contract, *, covered, cites_user_event):
    behaviors = set(contract.get("required_behaviors", []))
    criteria = {row["id"] for row in contract.get("acceptance_criteria", [])}
    exclusions = set(contract.get("scope_exclusions", []))
    id_tokens = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_]+[0-9]+(?![A-Za-z0-9_])")
    families = {
        re.match(r"[A-Za-z_]+", cid).group().casefold() for cid in criteria if re.match(r"[A-Za-z_]+[0-9]+$", cid)
    }

    def cites_defined_criterion(evidence):
        tokens = id_tokens.findall(evidence)
        cited = [token for token in tokens if re.match(r"[A-Za-z_]+", token).group().casefold() in families]
        known = any(
            re.search(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])", evidence) for cid in criteria
        )
        return known and all(token in criteria for token in cited)

    errors = []
    for row in requirements:
        entry = by_id[row["id"]]
        evidence = str(entry.get("evidence", "")).strip()
        disposition = entry["disposition"]
        if (
            covered
            and disposition == "covered"
            and evidence not in (behaviors | criteria)
            and not cites_defined_criterion(evidence)
        ):
            errors.append(f"Requirement {row['id']} is not covered by a behavior or criterion")
        if disposition == "excluded":
            if evidence not in exclusions:
                errors.append(f"Requirement {row['id']} is not present in scope_exclusions")
            if not cites_user_event(evidence):
                errors.append(f"Requirement {row['id']} cannot be excluded without a saved user event")
        if disposition == "superseded" and not cites_user_event(evidence):
            errors.append(f"Requirement {row['id']} cannot be superseded without a saved user event")
    return errors


def unrecorded_conflict_error(ids, conflict_sets):
    # The repair turn sees only this text, so name what would be accepted: a
    # saved answer that changes a requirement is not a recorded conflict, and
    # without the recorded sets the model keeps guessing at new ID groupings.
    message = "Conflict resolution must name exactly one recorded requirement conflict"
    if not conflict_sets:
        return (
            f"{message}: {', '.join(ids)} is not one, and no requirement conflict is recorded in the "
            "requirements handoff or its history, so conflict_resolutions must be []. Record a saved "
            "answer that changes a requirement in contract_changes or accepted_assumptions instead"
        )
    recorded = "; ".join("[" + ", ".join(sorted(group)) + "]" for group in sorted(sorted(g) for g in conflict_sets))
    return (
        f"{message}: {', '.join(ids)} is not one. Recorded conflicts: {recorded}. Resolve only those, "
        "each listed once with its exact requirement IDs, or leave conflict_resolutions []"
    )
