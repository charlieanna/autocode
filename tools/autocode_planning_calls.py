"""The cap on planning's model calls (owner decision 2026-11-12): how many planning has made, against the cap.

Planning is Requirements, the Planner and the Plan Reviewer, with their report repairs and the
Investigator's calls about a planning stage. Every attempt counts, one that timed out or whose answer
was rejected too, and a planning restart keeps the count. Only the person's own new input (an answer,
plan feedback, a message) starts the count again. A new run's cap is 20 calls; at the cap the run pauses
for the person before the next planning call, as the cost cap does, and they raise it
(`--max-planning-calls N`) or remove it (`0`). AutoCode never raises it itself. A run saved before the
cap existed has none.

Pure over the run's saved records: it reads, never writes.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_names as names
    from . import autocode_roles as roles
except ImportError:
    import autocode_names as names
    import autocode_roles as roles

STATUS = "PAUSED_PLANNING_CALL_LIMIT"
KEY = "planning_calls"  # settings.limits
FLAG = "--max-planning-calls"
DEST = "max_planning_calls"
DEFAULT = 20  # Planning calls of approved November 2026 runs: median 9, p95 19, max 25.
# The person's own new input. Approvals, resumes, limit changes and the runner's own records are not.
RENEWING_KINDS = frozenset(
    {"answer", "factual_answer", "brief_feedback", "plan_message", "gate_message", "run_message"}
)
PEOPLE = frozenset({"user_cli", "user_intervention"})
INVESTIGATOR = "investigate_stuck"
REPAIR_SUFFIX = "_report_repair"


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.timestamp() if parsed.utcoffset() is not None else None


def planning_stage(stage) -> bool:
    """Whether ``stage`` (a current or a saved legacy name, or its report repair) is a planning stage."""
    if not isinstance(stage, str) or not stage:
        return False
    return names.stage(stage.removesuffix(REPAIR_SUFFIX)) in roles.ALL_PLANNING_STAGES


def cap(state: RunState) -> int | None:
    """The saved cap, or None when the run has none (0, or a run saved before the cap existed)."""
    limits = (state.get("settings") or {}).get("limits")
    saved = limits.get(KEY) if isinstance(limits, dict) else None
    return saved if type(saved) is int and saved > 0 else None


def renewed_at(state: RunState) -> str | None:
    """When the person last gave new input, which starts the count again; None before any."""
    latest, text = None, None
    for event in state.get("user_events") or []:
        if not isinstance(event, dict) or event.get("kind") not in RENEWING_KINDS or event.get("actor") not in PEOPLE:
            continue
        moment = _time(event.get("at"))
        if moment is not None and (latest is None or moment >= latest):
            latest, text = moment, event["at"]
    return text


def _counts(moment, since) -> bool:
    # A record without a readable time counts: an unknown is never a call the cap forgets.
    return since is None or moment is None or moment >= since


def used(state: RunState) -> int:
    """The planning model calls since the person's last new input."""
    since = _time(renewed_at(state))
    calls = 0
    for row in state.get("stages") or []:
        if not isinstance(row, dict) or row.get("runner_owned") or row.get("dry_run"):
            continue
        if planning_stage(row.get("original_stage") or row.get("stage")) and _counts(
            _time(row.get("started_at")), since
        ):
            calls += 1
    for entry in state.get("stuck_investigations") or []:
        if not isinstance(entry, dict) or not entry.get("model") or not planning_stage(entry.get("stage")):
            continue
        if _counts(_time(entry.get("requested_at")), since):
            calls += 1
    return calls


def launching(state: RunState) -> bool:
    """Whether the next launch is a planning call: a planning stage, or the Investigator about one."""
    stage = state.get("next_stage")
    if stage == INVESTIGATOR:
        return planning_stage((state.get("stuck_investigation") or {}).get("stage"))
    return planning_stage(stage)


def reached(state: RunState) -> tuple[int, int] | None:
    """(used, cap) when the cap stops the next planning call, else None."""
    limit = cap(state)
    if limit is None or not launching(state):
        return None
    count = used(state)
    return (count, limit) if count >= limit else None


def stop_reason(count: int, limit: int) -> str:
    return (
        f"Planning made {count} model calls since your last answer or feedback, reaching the cap of {limit}; "
        f"raise it with {FLAG} N, or 0 for no cap, to continue"
    )


def view(state: RunState) -> dict:
    """The status view's `planning_calls`: the calls counted, the cap, and when the count last started again."""
    return {"used": used(state), "cap": cap(state), "renewed_at": renewed_at(state)}
