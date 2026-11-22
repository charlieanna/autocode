"""The saved handoff a rejected report was asked to answer."""

from __future__ import annotations

import copy
import itertools
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

    def has_saved_context(candidate):
        recorded = cast(
            "RunState",
            {**state, "settings": {**state["settings"], "roles": {**roles, role: candidate}}},
        )
        return saved == context(recorded, stage)

    # Preserve the existing exact recorded-route interpretation for older
    # records. New launch records project four fields with dict.get(), which
    # cannot distinguish an absent field from one explicitly set to None.
    if has_saved_context(copy.deepcopy(route)):
        return True
    current = roles[role]
    return isinstance(current, dict) and any(
        has_saved_context(candidate) for candidate in _projected_roles(current, route)
    )


def _projected_roles(current, route):
    """Invert only the recorded launch projection; the original context hash decides."""
    fields = ("engine", "provider", "model", "reasoning_effort")
    if (
        set(route) != set(fields)
        or not isinstance(route["model"], str)
        or not route["model"]
        or any(route[key] is not None and not isinstance(route[key], str) for key in fields)
    ):
        return
    # Preserve fields the launch record never projected. If any changed, the
    # independently saved context digest rejects every reconstructed role.
    base = {key: copy.deepcopy(value) for key, value in current.items() if key not in fields}
    base.update({key: route[key] for key in fields if route[key] is not None})
    absent_or_null = tuple(key for key in fields if route[key] is None)
    for present in itertools.product((False, True), repeat=len(absent_or_null)):
        candidate = copy.deepcopy(base)
        candidate.update({key: None for key, include in zip(absent_or_null, present, strict=True) if include})
        yield candidate
