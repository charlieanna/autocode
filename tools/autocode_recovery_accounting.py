"""Bounded automatic-recovery failures between valid saved stages.

Each automatic recovery that calls :func:`count` spends from this streak (a stage timeout or a
provider that failed to start, among others). After MAX_AUTOMATIC_RECOVERIES, or after
settings.limits.no_progress_batches timeout recoveries with no saved stage between them, the run
pauses (autocode_recovery_limits). A valid saved stage clears the streak, never usage or history. Capacity
retries are not counted here; they have their own lifetime cap
(autocode_stage_recovery.MAX_AUTOMATIC_CAPACITY_RECOVERIES).

An issued AutoResolver request binds the raw values of these keys (autocode_resolver_human._binding),
so a change between issue and answer makes the request stale and the answer is refused
(docs/bugs/2026-10-02-recovery-grant-request-binding.md). Reading therefore never writes a default,
so reads do not write defaults or corrections. A saved stage renews the streak;
an old held request can reconcile proven progress at the normal resume boundary.

The state keys:
- automatic_recoveries_since_resume: int, legacy name for failures since a valid saved stage. A run saved
  before the key existed, with timeout recoveries in its history, counts the larger of its two older
  counters instead; any other run without the key counts its consecutive timeout recoveries.
- consecutive_timeout_recoveries: int, timeout recoveries since the last saved stage
  (autocode_stage_recovery increments it).
- recovery_grants: list of {at, actor, amount, request_id, previous_count, remaining_count}.

Imports only utilities and the pure recovery-streak policy at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_streak as streak
    from . import autocode_util as util
except ImportError:
    import autocode_recovery_streak as streak
    import autocode_util as util

MAX_AUTOMATIC_RECOVERIES = 3


def spent(state: RunState) -> int:
    """Recovery failures since valid saved progress. Writes nothing.

    Every counted recovery writes automatic_recoveries_since_resume, so a run without it has spent
    none since the key existed: unchanged implementation batches in no_progress_batches are not
    recoveries (#448). Only a run saved before the key, with timeout recoveries in its history, also
    counted its recoveries in no_progress_batches.
    """
    if "automatic_recoveries_since_resume" in state:
        raw = state["automatic_recoveries_since_resume"]
        credit = streak.legacy_progress(state, raw)
        return credit["remaining_count"] if credit else raw
    consecutive = state.get("consecutive_timeout_recoveries", 0)
    if state.get("automatic_timeout_recoveries"):
        return max(consecutive, state.get("no_progress_batches", 0))
    return consecutive


def count(state: RunState) -> None:
    """Spend one automatic recovery."""
    state["automatic_recoveries_since_resume"] = spent(state) + 1


def consecutive_timeouts(state: RunState) -> int:
    """Timeout recoveries since the last saved stage. Writes nothing."""
    return state.get("consecutive_timeout_recoveries", 0)


def stage_saved(state: RunState) -> None:
    """A valid saved provider report ends the failure streak, retaining every debit."""
    records = state.get("stages") or []
    record = records[-1] if isinstance(records, list) and records else None
    if record is None or streak.saved_checkpoint(record):
        state["consecutive_timeout_recoveries"] = 0
    if streak.saved_checkpoint(record):
        state["automatic_recoveries_since_resume"] = 0


def progress_credit(state: RunState):
    return streak.legacy_progress(state, state.get("automatic_recoveries_since_resume"))


def record_grant(state: RunState, amount: int, request_id: str | None, spent_before: int) -> int:
    """Lower the spent count by an already validated grant of ``amount``; return what remains counted."""
    remaining = max(0, spent_before - amount)
    state["automatic_recoveries_since_resume"] = remaining
    state["consecutive_timeout_recoveries"] = 0
    state.setdefault("recovery_grants", []).append(
        {
            "at": util.now(),
            "actor": "user_cli",
            "amount": amount,
            "request_id": request_id,
            "previous_count": spent_before,
            "remaining_count": remaining,
        }
    )
    return remaining
