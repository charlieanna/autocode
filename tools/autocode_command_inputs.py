"""Typed human decisions for exact core-named commands; no grants or mutation.

Only finite total-active-time, planning-review and recovery-slot choices are
described here. Other placeholders remain unsupported until specified by core.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_accounting as accounting
except ImportError:
    import autocode_recovery_accounting as accounting

MAX_INTEGER = 9007199254740991  # Exact in the Python and JavaScript clients.
TOTAL_TIME_ACTION = "--resume-paused --max-seconds N"
PLANNING_ACTION = "--planning-review-call-limit N"
PLANNING_RESUME_ACTION = "--resume-paused " + PLANNING_ACTION
PLANNING_SAVE_THEN_RESUME = PLANNING_ACTION + " then --resume-paused"
GRANT_ACTION = "--resume-paused --grant-recovery N"


def project(state: RunState, need, resume_flags):
    """Describe one explicit finite total, never infer a value from human prose."""
    if state.get("status") == "PAUSED_PLANNING_BUDGET":
        return _planning(state, need, resume_flags)
    if state.get("status") == "PAUSED_TIMEOUT_RECOVERY":
        return _grant(state, need, resume_flags)
    if (
        state.get("status") != "PAUSED_TIME_LIMIT"
        or not need
        or need.get("kind") != "resume"
        or need.get("new_run_required")
        or need.get("edit_required")
        or (need.get("action") or resume_flags) != TOTAL_TIME_ACTION
    ):
        return None
    current = (state.get("settings") or {}).get("limits", {}).get("max_seconds")
    elapsed = state.get("active_seconds")
    if type(elapsed) not in (int, float):
        return None
    elapsed = cast(int | float, elapsed)
    if (
        type(current) is not int
        or not 0 < current < MAX_INTEGER
        or not 0 <= elapsed < MAX_INTEGER
        or not math.isfinite(elapsed)
    ):
        return None
    return [
        {
            "id": "max_seconds",
            "kind": "integer",
            "flag": "--max-seconds",
            "placeholder": "N",
            # Executable and the owned --run-dir/--workspace pairs removed.
            "arg_index": 2,
            "label": "Total active time (seconds)",
            "current": current,
            "elapsed": elapsed,
            "minimum": math.floor(max(current, elapsed)) + 1,
            "maximum": MAX_INTEGER,
        }
    ]


def _planning(state: RunState, need, resume_flags):
    action = (need or {}).get("action") or resume_flags or PLANNING_RESUME_ACTION
    if (
        not need
        or need.get("kind") != "planning_budget"
        or need.get("new_run_required")
        or need.get("edit_required")
        or action not in (PLANNING_ACTION, PLANNING_RESUME_ACTION, PLANNING_SAVE_THEN_RESUME)
        or state.get("next_stage") not in ("astra_challenge", "astra_finalize")
        or any(state.get(key) for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"))
    ):
        return None
    counts = _planning_counts(state)
    if counts is None:
        return None
    current, used = counts
    if used < current:
        return None
    return [
        {
            "id": "planning_review_call_limit",
            "kind": "integer",
            "flag": "--planning-review-call-limit",
            "placeholder": "N",
            "arg_index": 2 if action == PLANNING_RESUME_ACTION else 1,
            "label": "Total plan-review calls",
            "current": current,
            "used": used,
            "minimum": max(current, used) + 1,
            "maximum": MAX_INTEGER,
        }
    ]


def has_planning_allowance(state: RunState):
    """A saved finite choice already leaves review calls available; no new choice."""
    counts = _planning_counts(state)
    return counts is not None and counts[1] < counts[0]


def _planning_counts(state: RunState):
    planning = state.get("planning") or {}
    current = planning.get("review_call_limit", (state.get("settings") or {}).get("planning_review_call_limit", 2))
    used = planning.get("astra_calls")
    if (
        type(current) is not int
        or not 2 <= current < MAX_INTEGER
        or type(used) is not int
        or not 0 <= used < MAX_INTEGER
    ):
        return None
    return current, used


def _grant(state: RunState, need, resume_flags):
    if (
        not need
        or need.get("kind") != "resume"
        or need.get("new_run_required")
        or need.get("edit_required")
        or (need.get("action") or resume_flags) != GRANT_ACTION
        or any(
            state.get(key)
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
        )
    ):
        return None
    try:
        used = accounting.spent(state)
    except (TypeError, ValueError):
        return None
    if type(used) is not int or not 1 <= used <= MAX_INTEGER:
        return None
    return [
        {
            "id": "grant_recovery",
            "kind": "integer",
            "flag": "--grant-recovery",
            "placeholder": "N",
            "arg_index": 2,
            "label": "Additional recoveries",
            "current": accounting.MAX_AUTOMATIC_RECOVERIES,
            "used": used,
            "minimum": 1,
            "maximum": used,
        }
    ]
