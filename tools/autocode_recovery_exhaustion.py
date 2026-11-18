"""End a run whose automatic recovery allowance is spent, instead of asking a person to grant more.

When the fixed allowance of automatic recoveries (``recovery_accounting.MAX_AUTOMATIC_RECOVERIES``)
or the consecutive-timeout limit runs out, the run pauses ``PAUSED_TIMEOUT_RECOVERY``. The
Investigator looks first (``autocode_stuck_job``: one diagnosis per problem and source, which may
give back exactly one attempt). Once it has, nothing automatic remains. The run used to stop
there and ask a person to "authorize additional recoveries" with ``--grant-recovery N``, or wait
in an "AutoCode is blocked on internal recovery" pause with nothing to answer: both hand the
person AutoCode's own retries (AGENTS.md "What a person is asked").

``conclude`` instead ends the run with a recorded ``could_not_finish`` outcome
(``autocode_stop.end``, #2485) whose evidence names the recoveries used, the limit, the last cause
and the Investigator's look. A checkpoint saved at that pause by an earlier version, with or
without a held AutoResolver request about it, is ``pending``: the view shows ``continue`` and the
next resume concludes it (``settle``). The ``--grant-recovery`` CLI control is unchanged for an
operator who passes it before the run has ended; nothing offers it to a person.

Pure policy over saved state; imports nothing from the runner.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_accounting as accounting
    from . import autocode_recovery_context as recovery_context
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_resolver_human as human
    from . import autocode_stop as stop
except ImportError:
    import autocode_recovery_accounting as accounting
    import autocode_recovery_context as recovery_context
    import autocode_recovery_limits as recovery_limits
    import autocode_resolver_human as human
    import autocode_stop as stop

STATUS = "PAUSED_TIMEOUT_RECOVERY"
# What a saved checkpoint shows until its next resume ends it.
NOTICE = "AutoCode will close the spent automatic recovery allowance with a recorded outcome."


def spent(state: RunState) -> bool:
    """The automatic recovery allowance or the consecutive-timeout limit is used up."""
    try:
        return recovery_limits.allowance_spent(state, accounting.spent(state), accounting.MAX_AUTOMATIC_RECOVERIES)
    except (TypeError, ValueError):
        return False


def _settled(state: RunState) -> bool:
    return stop.applied_stop(state) is None and not any(state.get(key) for key in recovery_limits.UNSETTLED)


def _origin(proposal: Any) -> str | None:
    origin = (proposal or {}).get("origin") if isinstance(proposal, dict) else None
    return origin.get("pause_status") if isinstance(origin, dict) else None


def pending(state: RunState) -> dict | None:
    """A saved spent-recovery pause the next resume ends, or None. Nothing in it is for a person.

    The pause itself, the same pause with its operational request still queued at
    RESOLVER_PENDING, or published to a person at WAITING_FOR_USER."""
    if not _settled(state) or not spent(state):
        return None
    status = state.get("status")
    if status == STATUS:
        return {"origin": "paused", "request_id": None}
    if (queued := recovery_limits.queued_operational_hold(state)) and _origin(queued) == STATUS:
        return {"origin": "queued", "request_id": None}
    if (
        status == "WAITING_FOR_USER"
        and (issued := human.retained(state))
        and issued["scope"] == "operational_exhaustion"
    ):
        entry = state["resolver"]["human_escalations"][issued["request_id"]]
        if _origin(entry["identity"]["proposal"]) == STATUS:
            return {"origin": "published", "request_id": issued["request_id"]}
    return None


def conclude(state: RunState, *, at: str, cause: str | None = None, superseded_request_id: str | None = None) -> bool:
    """End the run with the spent allowance as its recorded outcome; True when it ended."""
    if not _settled(state) or not spent(state):
        return False
    used, maximum = accounting.spent(state), accounting.MAX_AUTOMATIC_RECOVERIES
    timeouts = accounting.consecutive_timeouts(state)
    last = recovery_context.exhaustion_cause(state)
    investigations = [
        copy.deepcopy(row) for row in state.get("stuck_investigations") or [] if row.get("status") == STATUS
    ]
    reason = (
        f"Automatic recovery could not get past a repeated stage failure: {used} of {maximum} automatic "
        f"recoveries used ({timeouts} consecutive timeouts). Last cause: {last}"
    )
    evidence: dict[str, Any] = {
        "pause_status": STATUS,
        "recoveries_used": used,
        "recoveries_allowed": maximum,
        "consecutive_timeouts": timeouts,
        "no_progress_limit": ((state.get("settings") or {}).get("limits") or {}).get("no_progress_batches", 3),
        "last_cause": last,
        "stage": state.get("next_stage"),
        "automatic_timeout_recoveries": copy.deepcopy(state.get("automatic_timeout_recoveries") or []),
        "investigations": investigations,
    }
    if cause:
        evidence["paused_with"] = cause
    if superseded_request_id:
        evidence["superseded_request_id"] = superseded_request_id
    stop.end(state, reason, evidence, at=at)
    return True


def conclude_pause(state: RunState, error, *, at: str) -> bool:
    """End the run at a live ``PAUSED_TIMEOUT_RECOVERY`` the Investigator has already taken or declined."""
    if getattr(error, "status", None) != STATUS:
        return False
    return conclude(state, at=at, cause=str(error))


def settle(state: RunState, *, at: str) -> bool:
    """At resume, withdraw any held request about the spent allowance and end the run."""
    found = pending(state)
    if not found or any(human.pending_interruptions(state.get("run_dir")).values()):
        return False
    if found["origin"] != "paused" and not human.supersede_operational(
        state, "AutoCode closed the spent automatic recovery allowance without a person's machinery decision"
    ):
        return False
    return conclude(state, at=at, superseded_request_id=found["request_id"])
