"""A person's change to a saved run's limit, recorded in the run's own event stream (#2412).

`autocode events` promises the run's configuration changes, but raising a limit on resume
(`resume --max-iterations 5` after an iteration-ceiling pause) saved the new bound in
`settings.limits` and recorded nothing. `record` appends one `limit_change` event per limit a
person's flag changed, beside `planning_budget_change`.

Two recoveries key on the *last* `user_events` entry: a `PAUSED_PLANNING_BUDGET` resume is
acknowledged only when the last event is its `planning_budget_change`, and a sealed draft contract
recovers only when the last event is its approval. A limit change between those steps is not a
decision about either, so they read `last_decision`, the last event that is not a limit change.

Domain layer: pure functions over saved state; imports nothing from AutoCode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

KIND = "limit_change"
# settings.limits key -> the main-run flag that sets it (autocode_configure applies the same table).
LIMIT_FLAGS = {
    "iteration_ceiling": "--max-iterations",
    "max_seconds": "--max-seconds",
    "max_cost_usd": "--max-cost-usd",
    "stage_timeout_seconds": "--max-stage-seconds",
    "idle_timeout_seconds": "--max-idle-seconds",
    "tool_timeout_seconds": "--max-tool-seconds",
    "no_progress_batches": "--no-progress-limit",
    "max_findings_per_task": "--max-findings-per-task",
}


def changes(previous: dict, selected: dict) -> list[dict[str, Any]]:
    """The limits a person's flag set to a new value: previous settings against the selected ones."""
    before = previous.get("limits") or {}
    after = selected.get("limits") or {}
    origins = selected.get("budget_origins") or {}
    rows = []
    for key, flag in LIMIT_FLAGS.items():
        if key not in after or origins.get(key) != "user_explicit" or (key in before and before[key] == after[key]):
            continue
        if key == "iteration_ceiling" and after[key] is None:
            flag = "--unlimited-iterations"
        rows.append({"limit": key, "flag": flag, "previous": before.get(key), "value": after[key]})
    return rows


def record(state: RunState, previous: dict, selected: dict, *, at: str) -> list[dict[str, Any]]:
    """Append one `limit_change` event per changed limit to the run's events; the events appended."""
    events = [
        {"kind": KIND, "actor": "user_cli", "at": at, **row, "stage": state.get("next_stage")}
        for row in changes(previous, selected)
    ]
    if events:
        state.setdefault("user_events", []).extend(events)
    return events


def last_decision(state: RunState) -> dict | None:
    """The run's last recorded event that is not a limit change, or None."""
    for event in reversed(state.get("user_events") or []):
        if not isinstance(event, dict) or event.get("kind") != KIND:
            return event
    return None
