"""Saved Investigator opt-in and bounds, independent of runtime job transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

MAX_CALLS = 3


def enabled(state: RunState) -> bool:
    return state.get("version", 2) >= 3 and max_calls(state) > 0


def default_calls(state: RunState) -> int:
    """MAX_CALLS, or one per approved milestone when the plan has more.

    A seven-milestone live run (2026-11-06) spent its three investigations on its planning and
    first wave, so a later wave's stalled Tester could only ask a person.
    """
    body = (state.get("goal_contract") or {}).get("body") or {}
    milestones = body.get("milestones") if isinstance(body, dict) else None
    return max(MAX_CALLS, len(milestones) if isinstance(milestones, list) else 0)


def max_calls(state: RunState) -> int:
    default = default_calls(state)
    limit = ((state.get("settings") or {}).get("stuck_investigation") or {}).get("max_calls_per_run", default)
    return limit if type(limit) is int and limit >= 0 else default
