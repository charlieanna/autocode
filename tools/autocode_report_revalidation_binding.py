"""The saved handoff a rejected report was asked to answer."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_report_repair_route as report_repair_route
    from . import autocode_util as util
except ImportError:
    import autocode_report_repair_route as report_repair_route
    import autocode_util as util


def context(state: RunState, stage):
    planning = state.get("planning") or {}
    settings = copy.deepcopy(state.get("settings") or {})
    # This route is allocated only to investigate the rejected report; it is
    # not an input to the original stage or its approved plan.
    if stage != "resolve_repair":
        report_repair_route.release_settings(settings, "repair_resolver")
    return util.digest(
        {
            "stage": stage,
            "task": state.get("task"),
            "task_id": state.get("task_id"),
            "current_task": state.get("current_task"),
            "goal_contract": state.get("goal_contract"),
            "contract_history": state.get("contract_history"),
            "task_archive": state.get("task_archive"),
            "answers": state.get("answers"),
            "feedback": state.get("brief_feedback"),
            "requirements_handoff": state.get("requirements_handoff"),
            "settings": settings,
            "turn": planning.get("turn_event_id"),
            "planning_reports": {
                key: row.get("report") for key, row in (planning.get("reports") or {}).items() if isinstance(row, dict)
            },
        }
    )


def matches(state: RunState, stage, saved, original) -> bool:
    """Whether ``saved`` binds this handoff, disregarding the runner's own route climb since then.

    After a rejected report the runner may climb its stage's route (a higher reasoning level for the
    next attempt). That route never produced the saved report: the original record's
    ``launch_route`` did. Read the context with that recorded route, so a validator fixed after the
    climb re-checks the saved report instead of paying for a fresh attempt (#3735). Every other
    input, and the pins, source and contract checks, stay exact.
    """
    if not saved:
        return False
    if saved == context(state, stage):
        return True
    route = original.get("launch_route") if isinstance(original, dict) else None
    role = original.get("role") if isinstance(original, dict) else None
    roles = (state.get("settings") or {}).get("roles")
    if not isinstance(route, dict) or not isinstance(role, str) or not isinstance(roles, dict) or role not in roles:
        return False
    recorded = cast(
        "RunState",
        {**state, "settings": {**state["settings"], "roles": {**roles, role: copy.deepcopy(route)}}},
    )
    return saved == context(recorded, stage)
