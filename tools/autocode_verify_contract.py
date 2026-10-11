"""Caller-declared acceptance criteria for verification of an external change."""

from __future__ import annotations

import json

try:
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_util as util
    import autocode_verification_plan as verification_plan


def load(text: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate criteria key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Nonfinite criteria value: {value}")

    value = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(value, dict) or set(value) - {"intended_outcome", "acceptance_criteria", "proof_kind"}:
        raise ValueError("Criteria must contain intended_outcome, acceptance_criteria and optional proof_kind")
    outcome = value.get("intended_outcome")
    criteria = value.get("acceptance_criteria")
    if not isinstance(outcome, str) or not outcome.strip():
        raise ValueError("intended_outcome must be nonempty")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("Supply at least one explicit acceptance criterion")
    seen = set()
    for row in criteria:
        if not isinstance(row, dict) or set(row) != {"id", "criterion", "verification_method", "human_review"}:
            raise ValueError("Each criterion requires id, criterion, verification_method and human_review")
        if any(
            not isinstance(row[key], str) or not row[key].strip() for key in ("id", "criterion", "verification_method")
        ):
            raise ValueError("Criterion IDs, text and methods must be nonempty strings")
        if row["id"] in seen or not isinstance(row["human_review"], bool):
            raise ValueError("Criterion IDs must be unique and human_review must be boolean")
        seen.add(row["id"])
        command = row["verification_method"]
        if verification_plan.commands(command) != [command]:
            raise ValueError(f"{row['id']}: verification_method must be one explicit executable check command")
    kind = value.get("proof_kind", "bugfix")
    if kind not in ("bugfix", "feature", "preserve"):
        raise ValueError("proof_kind must be bugfix, feature or preserve")
    result = {"intended_outcome": outcome, "acceptance_criteria": criteria, "proof_kind": kind}
    return {**result, "sha256": util.digest(result)}
