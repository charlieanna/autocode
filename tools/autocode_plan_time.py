"""Save the total active-time limit against the exact plan awaiting approval."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_budget_state as budget_state
    from . import autocode_goals as goals
except ImportError:
    import autocode_budget_state as budget_state
    import autocode_goals as goals


def change(runner, state: RunState, state_path, args) -> None:
    """Change only max_seconds; leave the displayed plan pending and launch no provider."""
    contract = state.get("goal_contract")
    if (
        state.get("status") != "AWAITING_GOAL_APPROVAL"
        or not contract
        or not args.expected_goal_token
        or args.expected_goal_token != goals.token(contract)
        or state.get("displayed_goal") != args.expected_goal_token
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
        )
    ):
        raise ValueError("The plan approval changed or is no longer at a safe boundary; refresh the status view")

    previous = copy.deepcopy(state["settings"])
    selected = copy.deepcopy(args._approval_time_settings)
    old_limits = previous.get("limits") or {}
    new_limits = selected.get("limits") or {}
    new_origins = selected.get("budget_origins") or {}
    old, new = old_limits.get("max_seconds"), new_limits.get("max_seconds")
    used = budget_state.active_seconds(state)
    if (
        type(old) is not int
        or old <= 0
        or type(new) is not int
        or new <= max(old, used)
        or new_origins.get("max_seconds") != "user_explicit"
    ):
        raise ValueError("Choose a larger finite total active-time limit than the current limit and time used")
    expected = copy.deepcopy(previous)
    expected.setdefault("limits", {})["max_seconds"] = new
    expected.setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
    if expected != selected:
        raise ValueError("Only the total active-time limit can change while the plan awaits approval")

    at = runner.now()
    state["settings"] = selected
    state.setdefault("configuration_changes", []).append(
        {
            "at": at,
            "previous": previous,
            "selected": copy.deepcopy(selected),
            "reason": "Total active-time limit extended at the pending plan approval; plan remains unapproved",
        }
    )
    state.setdefault("user_events", []).append(
        {
            "kind": "plan_time_limit_extended",
            "actor": "user_cli",
            "at": at,
            "goal_token": goals.token(contract),
            "from": old,
            "to": new,
            "active_seconds": used,
        }
    )
    runner.write_json(state_path, state)
