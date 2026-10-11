"""Saved Investigator opt-in and bounds, independent of runtime job transitions."""

MAX_CALLS = 3


def enabled(state: dict) -> bool:
    return state.get("version", 2) >= 3 and max_calls(state) > 0


def max_calls(state: dict) -> int:
    limit = ((state.get("settings") or {}).get("stuck_investigation") or {}).get("max_calls_per_run", MAX_CALLS)
    return limit if type(limit) is int and limit >= 0 else MAX_CALLS
