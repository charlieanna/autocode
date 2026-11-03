"""Restore an authenticated scope question hidden by a boundary pause.

Restoration acknowledges only the explicit pause. It answers no question and
retains source, contract and accounting authority checks from the original receipt.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_resolver_human as human
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_resolver_human as human
    import autocode_run_status as run_status


def _candidate(state: RunState):
    intent = state.get("pause_intent") or {}
    identifiers = intent.get("request_ids") or []
    receipts = state.get("applied_interventions") or []
    outstanding = [row for row in receipts if not row.get("resumed_at")]
    if (
        state.get("status") != "PAUSED_INTERVENTION"
        or not identifiers
        or len(identifiers) != len(set(identifiers))
        or intent.get("acknowledged_at")
        or intent.get("held_pause")
        or any(row.get("kind") == "stop" for row in receipts)
        or any(
            state.get(key)
            for key in (
                "stop_intent",
                "active_stage",
                "active_runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
                "job_failure",
                "current_task",
            )
        )
        or not outstanding
        or any(row.get("kind") != "pause" for row in outstanding)
        or sorted(row.get("id", "") for row in outstanding) != sorted(identifiers)
        or any(human.pending_interruptions(state.get("run_dir")).values())
    ):
        return None
    candidate = copy.deepcopy(state)
    run_status.pause(candidate, "WAITING_FOR_USER")
    try:
        published = human.current(candidate) or human.refresh_assigned_scope(candidate)
    except ValueError:
        return None
    if (
        not published
        or published.get("scope") not in ("clarification", "intake")
        or not published.get("questions")
        or any(not str(row.get("id", "")).startswith("workspace-scope-") for row in published["questions"])
    ):
        return None
    return candidate


def can_resume(state: RunState) -> bool:
    """Offer explicit restoration without changing the saved question or state."""
    return _candidate(state) is not None


def resume(state: RunState, *, at: str, acknowledge) -> bool:
    """Restore only a verified scope gate and acknowledge the pause transactionally."""
    candidate = _candidate(state)
    if candidate is None:
        return False
    acknowledge(candidate, at)
    candidate.pop("stop_reason", None)
    cast(dict, state).clear()
    state.update(candidate)
    return True
