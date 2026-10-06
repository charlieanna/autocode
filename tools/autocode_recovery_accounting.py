"""The automatic-recovery budget: what a run has spent since it last resumed, and the grants that replenish it.

Each automatic recovery that calls :func:`count` spends from this budget (a stage timeout or a
provider that failed to start, among others). After MAX_AUTOMATIC_RECOVERIES, or after
settings.limits.no_progress_batches timeout recoveries with no saved stage between them, the run
pauses (autocode_recovery_limits) until a person grants more (autocode_recovery_grants). Capacity
retries are not counted here; they have their own lifetime cap
(autocode_stage_recovery.MAX_AUTOMATIC_CAPACITY_RECOVERIES).

An issued AutoResolver request binds the raw values of these keys (autocode_resolver_human._binding),
so a change between issue and answer makes the request stale and the answer is refused
(docs/bugs/2026-10-02-recovery-grant-request-binding.md). Reading therefore never writes a default,
and only a grant validated against its request lowers the count.

The state keys:
- automatic_recoveries_since_resume: int, recoveries spent since the run last resumed. A run saved
  before the key existed, with timeout recoveries in its history, counts the larger of its two older
  counters instead; any other run without the key counts its consecutive timeout recoveries.
- consecutive_timeout_recoveries: int, timeout recoveries since the last saved stage
  (autocode_stage_recovery increments it).
- recovery_grants: list of {at, actor, amount, request_id, previous_count, remaining_count}.

Imports only autocode_util.
"""
from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MAX_AUTOMATIC_RECOVERIES = 3


def spent(state: dict) -> int:
    """The recoveries spent since the run last resumed. Writes nothing.

    Every counted recovery writes automatic_recoveries_since_resume, so a run without it has spent
    none since the key existed: unchanged implementation batches in no_progress_batches are not
    recoveries (#448). Only a run saved before the key, with timeout recoveries in its history, also
    counted its recoveries in no_progress_batches.
    """
    if "automatic_recoveries_since_resume" in state:
        return state["automatic_recoveries_since_resume"]
    consecutive = state.get("consecutive_timeout_recoveries", 0)
    if state.get("automatic_timeout_recoveries"):
        return max(consecutive, state.get("no_progress_batches", 0))
    return consecutive


def count(state: dict) -> None:
    """Spend one automatic recovery."""
    state["automatic_recoveries_since_resume"] = spent(state) + 1


def consecutive_timeouts(state: dict) -> int:
    """Timeout recoveries since the last saved stage. Writes nothing."""
    return state.get("consecutive_timeout_recoveries", 0)


def stage_saved(state: dict) -> None:
    """A saved stage ends a run of consecutive timeout recoveries."""
    state["consecutive_timeout_recoveries"] = 0


def record_grant(state: dict, amount: int, request_id: str | None, spent_before: int) -> int:
    """Lower the spent count by an already validated grant of ``amount``; return what remains counted."""
    remaining = max(0, spent_before - amount)
    state["automatic_recoveries_since_resume"] = remaining
    state["consecutive_timeout_recoveries"] = 0
    state.setdefault("recovery_grants", []).append({
        "at": util.now(), "actor": "user_cli", "amount": amount, "request_id": request_id,
        "previous_count": spent_before, "remaining_count": remaining})
    return remaining
