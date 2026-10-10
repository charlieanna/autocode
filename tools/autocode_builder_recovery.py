"""Explicit recovery of an exhausted serial Builder, preserving its retry history.

The dispatch adapter owns approval, worker-liveness checks and persistence.
No model route, contract, verification result or configured limit is changed.
"""

import copy

try:
    from . import autocode_builder_policy as policy
    from . import autocode_util as util
except ImportError:
    import autocode_builder_policy as policy
    import autocode_util as util


def request_serial_retry(state, selected, *, pause_status=None):
    """Grant one attempt to this stopped milestone; ordinary resume grants none."""
    task = state.get("current_task") or {}
    members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
    current = state.get("builder_retries", {}).get(policy.key(state))
    if (
        (pause_status or state.get("status")) != "PAUSED_BUILDER_RETRY_LIMIT"
        or state.get("next_stage") != "terra"
        or state.get("parent_run")
        or not policy.enabled(state)
        or not current
        or current.get("action") != "pause"
        or not current.get("failures")
    ):
        raise ValueError("Serial Builder retry requires an exhausted, stopped approved milestone")
    if not members or None in members or len(selected) != len(set(selected)) or set(selected) != set(members):
        raise ValueError("--retry-builder must name exactly the current serial milestone")
    if state.get("active_stage") or state.get("active_runner_check") or state.get("pending_report_repair"):
        raise ValueError("Reconcile the active attempt before retrying the serial Builder")
    pending = state.get("resolution_request")
    if pending and pending.get("contract_hash") != state["goal_contract"]["hash"]:
        raise ValueError("Pending Builder repair belongs to a different approved contract")
    if not pending and task.get("kind") != "implement":
        raise ValueError("Serial Builder retry requires an implementation task or a pending Resolver repair")
    route = state["settings"]["roles"]["terra"]
    at = util.now()
    current["action"] = "retry"
    state.setdefault("sessions", {}).pop("terra", None)
    state.setdefault("builder_retry_decisions", []).append(
        {
            "at": at,
            "owner": "user_cli",
            "action": "retry",
            "milestone_key": policy.key(state),
            "failure": current["failures"][-1],
            "attempt": len(current["failures"]),
            "reason": "Operator authorized one serial Builder retry after inspecting the failure",
            "selected_model": route["model"],
            "selected_effort": route.get("reasoning_effort"),
        }
    )
    state.setdefault("user_events", []).append(
        {
            "kind": "builder_retry",
            "actor": "user_cli",
            "at": at,
            "milestone_ids": list(selected),
            "mode": "serial",
            "failure": copy.deepcopy(current["failures"][-1]),
        }
    )
    # Exhaustion can precede assignment of the Resolver's proposed repair. Let
    # the ordinary Resolver path apply its contract/scope checks before building.
    state["next_stage"] = "astra_resolve" if pending else "terra"
