"""Explicit recovery grants validated across this invocation's settings change.

The old settings are supplied only by locked CLI setup. Rechecking the issued
request with that one prior field preserves all source, contract, permission,
history and worker bindings; an otherwise stale request earns no allowance.

Eligibility is shared with the stop advice (#288): a pause must never advertise
``--grant-recovery`` unless :func:`eligible` would accept it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_accounting as accounting
    from . import autocode_util as util
except ImportError:
    import autocode_recovery_accounting as accounting
    import autocode_util as util

# The #79 flag name is historical: a grant replenishes the whole automatic-recovery budget
# (autocode_recovery_accounting), not timeouts alone. Capacity retries have their own cap.
GRANTABLE_CAUSES = frozenset({"PAUSED_TIMEOUT_RECOVERY"})


def _cause(issued, escalations):
    if not issued:
        return None
    return (
        (escalations or {})
        .get(issued.get("request_id"), {})
        .get("identity", {})
        .get("proposal", {})
        .get("origin", {})
        .get("pause_status")
    )


def eligible(state: RunState, *, previous_settings=None, current_request, count, maximum, issued=None, cause=None):
    """Whether ``--grant-recovery`` would be accepted at this stop."""
    if state.get("status") == "PAUSED_TIMEOUT_RECOVERY":
        return True
    if issued is None:
        issued = current_request(state)
        if issued is None and previous_settings is not None:
            issued = current_request({**state, "settings": previous_settings})
    if cause is None:
        cause = _cause(issued, cast(dict, state.get("resolver", {})).get("human_escalations"))
    if not (issued and issued.get("scope") == "operational_exhaustion"):
        return False
    # An audited grant may replenish a spent automatic-recovery budget at the
    # operational-exhaustion stop that names it, whatever the originating pause.
    return cause in GRANTABLE_CAUSES or count >= maximum or _consecutive_exhausted(state)


def _consecutive_exhausted(state: RunState) -> bool:
    """The other counter ``recovery_limits.stop_reason`` publishes its exhaustion stop on.

    A stop reached by consecutive timeouts names ``--grant-recovery`` like any other
    budget exhaustion, so the grant must accept exactly that stop.
    """
    limit = (state.get("settings") or {}).get("limits", {}).get("no_progress_batches", 3)
    return bool(limit) and accounting.consecutive_timeouts(state) >= limit


def grant(
    state: RunState,
    run_dir,
    amount,
    *,
    previous_settings,
    current_request,
    count,
    supersede,
    persist,
    maximum,
    pending_limit_events=(),
):
    if type(amount) is not int or amount < 1:
        raise ValueError("recovery allowance must be a positive integer")
    issued = current_request(state)
    if issued is None and previous_settings is not None:
        issued = current_request({**state, "settings": previous_settings})
    if not eligible(
        state,
        previous_settings=previous_settings,
        current_request=current_request,
        count=count,
        maximum=maximum,
        issued=issued,
    ):
        raise ValueError("--grant-recovery requires a run paused for exhausted timeout recovery")
    request_id = issued["request_id"] if issued else None
    remaining = accounting.record_grant(state, amount, request_id, count)
    state.setdefault("user_events", []).append(
        {
            "kind": "recovery_grant",
            "at": util.now(),
            "actor": "user_cli",
            "amount": amount,
            "request_id": request_id,
            "previous_count": count,
        }
    )
    supersede(state, f"Operator granted {amount} more automatic recoveries")
    if pending_limit_events:
        # The invocation's limit_change events waited for the validation above (#2479): they join
        # the same single save as the grant and the corrected settings, so no exit can persist the
        # accepted grant without the configuration change that accompanied it (#2412).
        state.setdefault("user_events", []).extend(list(pending_limit_events))
    persist(run_dir / "state.json", state)
    print(
        f"Recovery grant recorded: {amount} more automatic timeout recoveries authorized "
        f"({count} -> {remaining} counted); history retained.",
        flush=True,
    )
    return remaining
