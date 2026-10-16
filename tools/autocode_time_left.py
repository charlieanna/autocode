"""What is left of the run's active-time limit and the current milestone's budget, stated for a person (#611).

A novelty hold (autocode_resolver_recovery.hold_stop) states these after the spend against the cost cap, so a person
deciding on the one scoped retry sees each budget the retry runs under:

- the run's active time (`active_seconds`) against the time limit the stage boundary enforces
  (`settings.limits.max_seconds`, whatever set it: `--max-seconds`, the runner's default or AutoResolver's
  one-time extension). No limit (0 or none saved), no line.
- with milestone checkpoints on, the current milestone's counted time against its budget
  (`settings.milestone_checkpoints.max_seconds`), which autocode_milestones.check_budget enforces before a writer
  launch. No line with no budget saved, while a progressive run's slice budget governs instead (as
  autocode_climb_cap.milestone_crossing reads it), when the current task is not on the approved contract, or
  before the milestone has a saved row (as autocode_milestones.progress reads it; the row is never created here).

Pure over saved state; imports only autocode_milestone_scope.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_milestone_scope as milestone_scope
except ImportError:
    import autocode_milestone_scope as milestone_scope


def _seconds(value):
    """A saved count of seconds, or None when it is not a finite nonnegative number."""
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _shown(seconds):
    """1,250 s; a fraction to one place, as autocode_climb_cap states a budget."""
    return f"{seconds:,.1f}".rstrip("0").rstrip(".") + " s"


def _section(state: RunState, key):
    settings = state.get("settings")
    value = settings.get(key) if isinstance(settings, dict) else None
    return value if isinstance(value, dict) else {}


def run_time(state: RunState):
    """{"used", "limit"}: the run's active time and its time limit in seconds, or None with no limit."""
    limit = _seconds(_section(state, "limits").get("max_seconds"))
    if not limit:
        return None
    return {"used": _seconds(state.get("active_seconds")) or 0, "limit": limit}


def milestone_time(state: RunState):
    """{"milestones", "used", "limit"}: the current milestone's (or batch's) ids, counted time and budget in
    seconds, or None when that budget does not govern the current task (see the module docstring)."""
    checkpoints = _section(state, "milestone_checkpoints")
    limit = _seconds(checkpoints.get("max_seconds"))
    if checkpoints.get("enabled") is not True or not limit:
        return None
    progressive = state.get("progressive")
    if isinstance(progressive, dict) and progressive.get("budget"):
        return None
    task = state.get("current_task") or {}
    if not task or task.get("contract_hash") != (state.get("goal_contract") or {}).get("hash"):
        return None
    row = (state.get("milestone_progress") or {}).get(milestone_scope.key(state))
    if not isinstance(row, dict):
        return None
    ids = row.get("milestone_ids") or [row.get("id") or task.get("milestone_id") or ""]
    return {"milestones": sorted(ids), "used": _seconds(row.get("seconds")) or 0, "limit": limit}


def lines(state: RunState):
    """The sentences stating run_time and milestone_time, in that order; empty when neither applies."""
    stated = []
    if run := run_time(state):
        stated.append(f"Active time: {_shown(run['used'])} of the {_shown(run['limit'])} limit.")
    if milestone := milestone_time(state):
        several = len(milestone["milestones"]) > 1
        stated.append(
            f"Milestone{'s' if several else ''} {', '.join(milestone['milestones'])}: {_shown(milestone['used'])} "
            f"of {'their' if several else 'its'} {_shown(milestone['limit'])} budget."
        )
    return stated
