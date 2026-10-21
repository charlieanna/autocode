"""Select report-only planning recovery without changing its admission or budgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def active(state: RunState, stage):
    """True only for this review's already-authorized recovery, never ordinary review.

    Admission and evidence binding remain owned by the existing recovery path.
    This selector only narrows tools; it cannot grant a call or accept a report.
    """
    if stage not in ("astra_challenge", "astra_finalize"):
        return False
    recovery = state.get("recovery_context") or {}
    if recovery.get("stage") == stage and recovery.get("next_stage", stage) == stage:
        return True
    return any(
        not grant.get("consumed") and grant.get("binding", {}).get("stage") == stage
        for grant in (state.get("planning") or {}).get("recovery_review_grants", [])
    )
