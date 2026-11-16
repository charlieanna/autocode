"""Explicit recovery of an exhausted serial Builder, preserving its retry history.

The dispatch adapter owns approval, worker-liveness checks and persistence.
No model route, contract, verification result or configured limit is changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy

try:
    from . import autocode_builder_policy as policy
    from . import autocode_util as util
except ImportError:
    import autocode_builder_policy as policy
    import autocode_util as util


def request_serial_retry(state: RunState, selected, *, pause_status=None):
    """Grant one attempt to this stopped milestone; ordinary resume grants none."""
    task = state.get("current_task") or {}
    members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
    current = state.get("builder_retries", {}).get(policy.key(state))
    if (
        (pause_status or state.get("status")) != "PAUSED_BUILDER_RETRY_LIMIT"
        or state.get("next_stage") != "builder"
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
    if pending and pending.get("contract_hash") != cast(dict, state["goal_contract"])["hash"]:
        raise ValueError("Pending Builder repair belongs to a different approved contract")
    if not pending and task.get("kind") != "implement":
        raise ValueError("Serial Builder retry requires an implementation task or a pending Resolver repair")
    route = state["settings"]["roles"]["builder"]
    at = util.now()
    current["action"] = "retry"
    state.setdefault("sessions", {}).pop("builder", None)
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
    state["next_stage"] = "resolve" if pending else "builder"


def serial_retry_outcome(state: RunState, selected):
    """The committed serial owner's exact decision, independent of its target stage."""
    task = state.get("current_task") or {}
    members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
    key = policy.key(state)
    current = (state.get("builder_retries") or {}).get(key) or {}
    decisions = state.get("builder_retry_decisions") or []
    decision = decisions[-1] if isinstance(decisions, list) and decisions else None
    event = next(
        (
            event
            for event in reversed(state.get("user_events") or [])
            if isinstance(event, dict) and event.get("kind") == "builder_retry"
        ),
        None,
    )
    pending = state.get("resolution_request")
    target = "resolve" if pending else "builder"
    route = (state.get("settings") or {}).get("roles", {}).get("builder") or {}
    if (
        state.get("parent_run")
        or not policy.enabled(state)
        or current.get("action") != "retry"
        or not current.get("failures")
        or not members
        or None in members
        or len(selected) != len(set(selected))
        or set(selected) != set(members)
        or any(state.get(key) for key in ("active_stage", "active_runner_check", "pending_report_repair"))
        or pending
        and pending.get("contract_hash") != (state.get("goal_contract") or {}).get("hash")
        or not pending
        and task.get("kind") != "implement"
        or state.get("next_stage") != target
        or not isinstance(decision, dict)
        or decision.get("owner") != "user_cli"
        or decision.get("action") != "retry"
        or decision.get("milestone_key") != key
        or decision.get("failure") != current["failures"][-1]
        or decision.get("attempt") != len(current["failures"])
        or decision.get("selected_model") != route.get("model")
        or decision.get("selected_effort") != route.get("reasoning_effort")
        or not isinstance(event, dict)
        or event.get("actor") != "user_cli"
        or event.get("mode") != "serial"
        or event.get("milestone_ids") != list(selected)
        or event.get("failure") != decision.get("failure")
        or event.get("at") != decision.get("at")
    ):
        raise ValueError("The serial Builder retry no longer matches its exact committed decision")
    return {
        "mode": "serial",
        "milestone_key": key,
        "decision": copy.deepcopy(decision),
        "event": copy.deepcopy(event),
        "pending": util.digest(pending),
        "task": util.digest(task),
        "contract": util.digest(state.get("goal_contract")),
        "target": target,
    }
