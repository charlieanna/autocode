"""Correct pre-build planning recoveries miscounted as implementation batches."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_accounting as accounting
    from . import autocode_roles as roles
except ImportError:
    import autocode_recovery_accounting as accounting
    import autocode_roles as roles

PLANNING = frozenset(roles.ALL_PLANNING_STAGES)
PRE_BUILD = PLANNING | {"recognize_workflow"}


def routine_resume(state: RunState, issued):
    """An old exhausted request whose own ledger proves intervening success."""
    if not issued or issued.get("scope") != "operational_exhaustion":
        return None
    entry = (state.get("resolver") or {}).get("human_escalations", {}).get(issued.get("request_id"), {})
    origin = entry.get("identity", {}).get("proposal", {}).get("origin", {})
    if (
        entry.get("status") != "pending"
        or origin.get("pause_status") != "PAUSED_TIMEOUT_RECOVERY"
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
        )
    ):
        return None
    credit = accounting.progress_credit(state)
    limit = (state.get("settings") or {}).get("limits", {}).get("no_progress_batches", 3)
    if (
        not credit
        or credit["previous_count"] < accounting.MAX_AUTOMATIC_RECOVERIES
        or credit["remaining_count"] >= accounting.MAX_AUTOMATIC_RECOVERIES
        or (limit and accounting.consecutive_timeouts(state) >= limit)
    ):
        return None
    return credit


def planning_only_count(state: RunState):
    """Prove an old counter contains no implementation attempts, or decline."""
    count = state.get("no_progress_batches", 0)
    if type(count) is not int or count <= 0:
        return None
    if any(
        state.get(key)
        for key in (
            "active_stage",
            "pending_report_repair",
            "uncertain_artifacts",
            "implementation",
            "no_progress_reports",
            "orchestration_batch",
            "orchestration_history",
        )
    ):
        return None
    stages = state.get("stages") or []
    if not stages or any(
        (row.get("original_stage") or row.get("stage")) not in PRE_BUILD
        for row in stages
        if not row.get("runner_owned")
    ):
        return None
    recovered = [*state.get("automatic_timeout_recoveries", []), *state.get("automatic_permission_recoveries", [])]
    attempts = {
        row.get("attempt_id")
        for row in recovered
        if row.get("stage") in PLANNING and row.get("attempt_id") and not row.get("changed_files")
    }
    return sorted(attempts) if len(attempts) >= count else None


def reconcile(state: RunState, *, issued, approved, supersede, now):
    """One explicit resume may withdraw only a verified, false pre-build hold.

    The caller verifies the current request under the run lock. Unknown history,
    any implementation attempt and stale requests retain their existing hold.
    Timeout allowances and all original attempt evidence remain unchanged.
    """
    if not issued or issued.get("scope") != "operational_exhaustion" or not approved:
        return False
    credit = routine_resume(state, issued)
    if credit:
        reason = "A valid saved stage ended the old run-wide recovery streak; history and usage retained"
        if not supersede(state, reason):
            return False
        state["automatic_recoveries_since_resume"] = credit["remaining_count"]
        state.setdefault("user_events", []).append(
            {
                "kind": "recovery_accounting_correction",
                "actor": "runner",
                "at": now(),
                "request_id": issued["request_id"],
                **credit,
                "reason": reason,
            }
        )
        state.update(status="RUNNING", phase=state.get("phase") or "READY_TO_EXECUTE")
        state.pop("stop_reason", None)
        return True
    entry = cast(dict, state.get("resolver", {})).get("human_escalations", {}).get(issued["request_id"], {})
    origin = entry.get("identity", {}).get("proposal", {}).get("origin", {})
    if origin.get("pause_status") != "PAUSED_NO_PROGRESS":
        return False
    attempts = planning_only_count(state)
    if attempts is None:
        return False
    reason = "Planning recoveries are not unchanged implementation batches; no Builder has run"
    if not supersede(state, reason):
        return False
    state.setdefault("user_events", []).append(
        {
            "kind": "recovery_accounting_correction",
            "actor": "runner",
            "at": now(),
            "request_id": issued["request_id"],
            "previous_no_progress_batches": state["no_progress_batches"],
            "planning_attempts": attempts,
            "reason": reason,
        }
    )
    state.update({"no_progress_batches": 0, "status": "RUNNING", "phase": "READY_TO_EXECUTE"})
    state.pop("stop_reason", None)
    return True
