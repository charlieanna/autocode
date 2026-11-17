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


PLANNING_REPORT_STOPS = ("PAUSED_REPEATED_FAILURE", "PAUSED_REPORT_REPAIR_LIMIT")


def unapproved_planning_contract(contract: dict | None) -> bool:
    return not isinstance(contract, dict) or (
        contract.get("approval_status") != "approved" and not contract.get("approval_event")
    )


def planning_report_stop(state: RunState, status: str | None = None) -> bool:
    """A settled, unapproved planning-report non-convergence stop may restart discovery via brief feedback."""
    target = state.get("status") if status is None else status
    return bool(
        target in PLANNING_REPORT_STOPS
        and state.get("next_stage") in roles.ALL_PLANNING_STAGES
        and unapproved_planning_contract(state.get("goal_contract"))
        and not (state.get("recovery_context") or {}).get("denied_operation")
        and not any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
        and (state.get("user_request") or {}).get("kind") not in ("human_review", "blocker")
    )


def planning_incident_acknowledges(evidence: dict | None, binding: dict | None) -> bool:
    """Whether an operational incident recorded at ``evidence``/``binding`` is an unapproved planning-report stop."""
    if not isinstance(evidence, dict) or not isinstance(binding, dict):
        return False
    return bool(
        evidence.get("status") in PLANNING_REPORT_STOPS
        and evidence.get("stage") in roles.ALL_PLANNING_STAGES
        and unapproved_planning_contract(binding.get("contract"))
    )


def invalid_report(state: RunState) -> bool:
    return (
        state.get("status") == "PAUSED_INVALID_OUTPUT"
        and state.get("next_stage") in (*roles.ALL_PLANNING_STAGES, "write_acceptance_tests")
        and (state.get("user_request") or {}).get("kind") not in ("human_review", "blocker")
    ) or planning_report_stop(state, state.get("status"))


def semantic_stop(state: RunState, status: str | None) -> bool:
    """A stopped semantic decision can take a revised brief, subject to pause authority."""
    return (
        status == state.get("status")
        and status in ("RESOLVER_PENDING", "PAUSED_COMPLETION_GATE")
        and (state.get("user_request") or {}).get("kind") not in ("human_review", "blocker")
    )


def between_stages(state: RunState) -> bool:
    """An idle draft handoff may take new context before its next planning call."""
    contract = state.get("goal_contract") or {}
    return bool(
        state.get("status") == "RUNNING"
        and state.get("phase") == "PLANNING"
        and state.get("next_stage") in roles.ALL_PLANNING_STAGES
        and contract
        and contract.get("approval_status") != "approved"
        and not contract.get("approval_event")
        and not any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
                "pending_questions",
                "user_request",
            )
        )
    )


def admitted(state: RunState) -> bool:
    """Conversation checkpoints and correctable planning/semantic stops.

    Callers still enforce pause authority and human artifact-review decisions.
    """
    return (
        state.get("status") in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", *run_status.PLAN_FEEDBACK_PAUSES)
        or (not state.get("goal_contract") and roles.planning_flow(state) == "v2")
        or invalid_report(state)
        or semantic_stop(state, state.get("status"))
        or between_stages(state)
    )
