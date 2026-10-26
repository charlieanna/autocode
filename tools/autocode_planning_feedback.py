"""The planning-report checkpoint that can take corrective brief input.

This describes the checkpoint only. CLI writers and status projections still
apply the shared pause-authority guard; a correction grants no retry or approval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_roles as roles
    import autocode_run_status as run_status


def invalid_report(state: RunState) -> bool:
    return (
        state.get("status") == "PAUSED_INVALID_OUTPUT"
        and state.get("next_stage") in roles.ALL_PLANNING_STAGES
        and (state.get("user_request") or {}).get("kind") not in ("human_review", "blocker")
    )


def admitted(state: RunState) -> bool:
    """Existing conversation checkpoints plus a malformed planning report.

    Callers still enforce pause authority and human artifact-review decisions.
    """
    return (
        state.get("status") in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", *run_status.PLAN_FEEDBACK_PAUSES)
        or (not state.get("goal_contract") and roles.planning_flow(state) == "v2")
        or invalid_report(state)
    )
