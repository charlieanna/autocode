"""Reject one pending artifact review through the existing durable feedback boundary.

A rejection is corrective input, never an approval or execution grant. The same
brief-feedback receipt is saved in user_events and brief_feedback, with the exact
criterion/artifact binding attached. Existing planning consumes its note and must
produce a refreshed plan for explicit approval before building again.
"""

try:
    from . import autocode_goals as goals
    from . import autocode_human_review_artifact as artifact
except ImportError:
    import autocode_goals as goals
    import autocode_human_review_artifact as artifact


def reject(state, criterion, selected, current, note):
    """Validate a pending review, then retain its rejection as planning feedback."""
    if not isinstance(note, str) or not note.strip():
        raise ValueError("Rejecting an artifact review requires a nonempty review note")
    goals.execution_guard(state)
    artifact.validate(state, criterion, selected, current, action="rejection")
    request = state.get("user_request") or {}
    if (
        state.get("status") != "WAITING_FOR_USER"
        or request.get("kind") != "human_review"
        or criterion not in goals.requested_review_criteria(state, request)
        or criterion not in goals.missing_human_reviews(state)
    ):
        raise ValueError("Only a pending criterion in the current artifact review can be rejected")
    # All checks precede the standard feedback mutation. Its receipt, source log,
    # clarification episode and approval/evidence invalidation stay authoritative.
    state.pop("user_request", None)
    goals.feedback(state, note)
    state["brief_feedback"][-1]["review_rejection"] = {"criterion": criterion, "token": selected}
