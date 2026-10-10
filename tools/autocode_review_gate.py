"""Recognize a review-only model request without treating it as user authority."""

from __future__ import annotations


def review_only_permission(report: dict, request: dict, required: set[str]) -> bool:
    """Match an explicit, scope-preserving request for a declared human review."""
    if (
        not required
        or report.get("status") != "BLOCKED"
        or report.get("findings")
        or report.get("user_request") != request
        or request.get("kind") != "permission"
    ):
        return False
    decision = request.get("decision_needed", "").lower()
    delta = request.get("proposed_delta", "").lower()
    return (
        "human review" in decision
        and "runner" in decision
        and all(criterion.lower() in decision for criterion in required)
        and delta.startswith("no change to the approved goal")
        and "product behavior" in delta
    )
