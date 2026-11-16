"""The saved handoff a rejected report was asked to answer."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

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
