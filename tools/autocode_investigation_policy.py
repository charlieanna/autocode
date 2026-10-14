"""Saved Investigator opt-in and bounds, independent of runtime job transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

MAX_CALLS = 3


def enabled(state: RunState) -> bool:
    return state.get("version", 2) >= 3 and max_calls(state) > 0


def max_calls(state: RunState) -> int:
    limit = ((state.get("settings") or {}).get("stuck_investigation") or {}).get("max_calls_per_run", MAX_CALLS)
    return limit if type(limit) is int and limit >= 0 else MAX_CALLS
