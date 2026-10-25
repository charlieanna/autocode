"""Typed human decisions for exact core-named commands; no grants or mutation.

Each finite number a pause leaves to a person is described here (#2044): the
planning-review allowance, the recovery grant, and every exhaustible bound a
budget pause names in BOUNDS (active time, cost cap, iterations, milestone time,
milestone replans, unchanged batches). A bound is described wherever it holds
the run: at its own pause, after AutoResolver published its operational
request, and while held information names the raise. Where `next_command` is
the raise, the row binds to it. Where it is not (a plain resume, an operational
response), the row is `separate` and the status view names its own `command`.
Nothing here grants anything, chooses a value or changes state.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_milestone_scope as milestone_scope
    from . import autocode_pause_authority as pause_authority
    from . import autocode_planning_review_usage as review_usage
    from . import autocode_recovery_accounting as accounting
    from . import autocode_recovery_limits as recovery_limits
except ImportError:
    import autocode_cost as cost
    import autocode_milestone_scope as milestone_scope
    import autocode_pause_authority as pause_authority
    import autocode_planning_review_usage as review_usage
    import autocode_recovery_accounting as accounting
    import autocode_recovery_limits as recovery_limits

MAX_INTEGER = 9007199254740991  # Exact in the Python and JavaScript clients.
TOTAL_TIME_ACTION = "--resume-paused --max-seconds N"
PLANNING_ACTION = "--planning-review-call-limit N"
PLANNING_RESUME_ACTION = "--resume-paused " + PLANNING_ACTION
PLANNING_SAVE_THEN_RESUME = PLANNING_ACTION + " then --resume-paused"
GRANT_ACTION = "--resume-paused --grant-recovery N"


# Run activity that makes any typed bound stale: the CLI reconciles it first.
BUSY = ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")


def _number(value):
    if type(value) is int:
        return 0 <= value < MAX_INTEGER
    return type(value) is float and math.isfinite(value) and 0 <= value < MAX_INTEGER


def _limits(state: RunState):
    limits = (state.get("settings") or {}).get("limits")
    return limits if isinstance(limits, dict) else {}


def _checkpoints(state: RunState):
    saved = (state.get("settings") or {}).get("milestone_checkpoints")
    return saved if isinstance(saved, dict) else {}


def _milestone_row(state: RunState):
    """The current milestone's saved progress row, read only (autocode_milestones.progress creates one)."""
    row = (state.get("milestone_progress") or {}).get(milestone_scope.key(state))
    return row if isinstance(row, dict) else {}


def _time(state: RunState):
    return _limits(state).get("max_seconds"), state.get("active_seconds")


def _cost(state: RunState):
    return cost.cap(state.get("settings")), cost.spent(state)


def _iterations(state: RunState):
    return _limits(state).get("iteration_ceiling"), state.get("iteration")


def _milestone_seconds(state: RunState):
    return _checkpoints(state).get("max_seconds"), _milestone_row(state).get("seconds", 0)


def _milestone_replans(state: RunState):
    return _checkpoints(state).get("max_replans", 1), _milestone_row(state).get("replans", 0)


def _no_progress(state: RunState):
    if not recovery_limits.no_progress_bound_holds(state):
        return None, None
    return _limits(state).get("no_progress_batches", 3), state.get("no_progress_batches", 0)


# id, flag, label, what `used` is called, and (current, used) from saved state.
_TIME = ("max_seconds", "--max-seconds", "Total active time (seconds)", "elapsed", _time)
_MILESTONE_SECONDS = (
    "max_milestone_seconds",
    "--max-milestone-seconds",
    "Milestone active time (seconds)",
    "used",
    _milestone_seconds,
)
BOUNDS = {
    "PAUSED_TIME_LIMIT": _TIME,
    "PAUSED_COST_LIMIT": ("max_cost_usd", "--max-cost-usd", "Cost cap (whole USD)", "used", _cost),
    "PAUSED_ITERATION_LIMIT": ("max_iterations", "--max-iterations", "Total iterations", "used", _iterations),
    "PAUSED_MILESTONE_TIME_LIMIT": _MILESTONE_SECONDS,
    "PAUSED_MILESTONE_BUDGET": _MILESTONE_SECONDS,
    "PAUSED_MILESTONE_STALLED": (
        "max_milestone_replans",
        "--max-milestone-replans",
        "Milestone replans",
        "used",
        _milestone_replans,
    ),
    "PAUSED_NO_PROGRESS": (
        "no_progress_limit",
        "--no-progress-limit",
        "Unchanged implementation batches allowed",
        "used",
        _no_progress,
    ),
}
# Every flag whose N a person types into a core-named command.
PERSON_FLAGS = tuple(
    dict.fromkeys([*(spec[1] for spec in BOUNDS.values()), "--planning-review-call-limit", "--grant-recovery"])
)


def raise_action(row):
    """The resume that applies a bound row's typed value: `--resume-paused FLAG N`."""
    return f"--resume-paused {row['flag']} N"


def project(state: RunState, need, resume_flags):
    """Describe one explicit finite total, never infer a value from human prose."""
    if state.get("status") == "PAUSED_PLANNING_BUDGET":
        return _planning(state, need, resume_flags)
    if state.get("status") == "PAUSED_TIMEOUT_RECOVERY":
        return _grant(state, need, resume_flags)
    return _bound(state, need, resume_flags)


def _bound(state: RunState, need, resume_flags):
    """The bound holding the run, when a person's number for it is the way on.

    Attached when the named action is that bound's raise; `separate` (the view names
    its own command) when the named action is a plain resume or AutoResolver's
    operational request for that pause. Any other named control (a retry, an edit,
    an inspection, a model question) is that pause's way on, so no number is offered.
    """
    pause = pause_authority.held_origin(state).get("pause_status")
    spec = BOUNDS.get(pause)
    if (
        not spec
        or not need
        or need.get("new_run_required")
        or need.get("edit_required")
        or need.get("route")
        or any(state.get(key) for key in BUSY)
    ):
        return None
    identity, flag, label, used_name, read = spec
    named = need.get("action") or resume_flags
    if need.get("kind") == "resume" and named == f"--resume-paused {flag} N":
        separate = False
    elif (need.get("kind") == "resume" and named in (None, "--resume-paused")) or (
        need.get("kind") == "answer"
        and need.get("resolver_scope") == "operational_exhaustion"
        and need.get("pause_origin") == pause
    ):
        separate = True
    else:
        return None
    current, used = read(state)
    if type(current) is bool or type(used) is bool or not _number(current) or not _number(used):
        return None
    if current == 0 and identity != "max_iterations":
        return None  # 0 means no limit there, so it cannot be the bound that paused the run
    row = {
        "id": identity,
        "kind": "integer",
        "flag": flag,
        "placeholder": "N",
        # Executable and the owned --run-dir/--workspace pairs removed.
        "arg_index": 2,
        "label": label,
        "current": current,
        used_name: used,
        "minimum": math.floor(max(current, used)) + 1,
        "maximum": MAX_INTEGER,
    }
    if separate:
        row["command"] = None  # autocode_run_view names it, with the run's paths and recovery token
    return [row]


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
    used = review_usage.effective_calls(state)
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
