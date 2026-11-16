"""Per-task Cascade effort sizing for the Builder (#1710 + per-task ceiling & difficulty).

After plan approval, Requirements, the Planner and the Plan Reviewer have already fixed the
contract and file boundaries:

- On a bounded leaf task (1..4 files, none matching ``sensitive-paths``), the first Builder attempt
  steps down: to ``low`` on runner/selector defaults, or to the highest level the model offers below
  the saved stage-choice ceiling (e.g. ``high -> medium`` on MiMo/Sonnet, ``high -> low`` on GLM-5.3),
  including leaf milestones inside multi-milestone plans.
- On a second attempt at the same task or milestone, the Builder restores its configured ceiling.
- On a further retry at the same task/milestone, or upfront on a hard task (6+ files or touching
  ``sensitive-paths``), the Builder climbs one offered level above its configured level on the same
  model when available.

``AUTOCODE_LIGHT_BUILDER=0`` turns the rule off. An effort a person typed explicitly as a flag
(``user_explicit``), a cross-model stronger attempt, a climbed route, or an unrecorded provenance is
left untouched.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_role_route as role_route
    from . import autocode_route_fit as route_fit
    from . import autocode_sensitive_paths as sensitive_paths
except ImportError:
    import autocode_role_route as role_route
    import autocode_route_fit as route_fit
    import autocode_sensitive_paths as sensitive_paths

STAGE = "builder"
LOW = "low"
SMALL_PATHS = 4  # files the current task may touch and still count as bounded
HARD_PATHS = 6  # files at or above which a single build task counts as wide/hard
SETTING = "AUTOCODE_LIGHT_BUILDER"
_ABOVE_LOW = ("medium", "high", "xhigh", "max")
_ORIGINS = (role_route.STAGE_CHOICE, role_route.RUNNER_DEFAULT, role_route.AUTO_SELECTED)
_SIGNAL_TEXT = {
    "wide_task": "it touches six or more files",
    "sensitive_paths": "it touches sensitive files",
}


def effort(state: RunState, stage, report_only, configured):
    """The reasoning effort for this launch: per-task Cascade level for Builder, else ``configured``."""
    if stage != STAGE or report_only or configured not in route_fit.SCALE or os.environ.get(SETTING) == "0":
        return configured
    settings = state.get("settings") or {}
    origin = (settings.get("effort_origins") or {}).get(STAGE)
    if origin == role_route.STAGE_CHOICE:
        route = (settings.get("roles") or {}).get(STAGE) or {}
        decision = decide(state, stage, STAGE, report_only, configured, model=route.get("model"))
        return decision["level"] if decision else configured
    return LOW if applies(state, stage, report_only, configured) else configured


def applies(state: RunState, stage, report_only, configured) -> bool:
    if stage != STAGE or report_only or configured not in _ABOVE_LOW or os.environ.get(SETTING) == "0":
        return False
    settings = state.get("settings") or {}
    if not role_route.effort_can_reduce(settings, STAGE):
        return False
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "approved":
        return False
    task = state.get("current_task") or {}
    paths = task.get("affected_paths") or []
    if not task.get("id") or not paths or len(paths) > SMALL_PATHS or _hard_signals(settings, paths):
        return False
    return _prior_attempts(state, task) == 0


def decide(state: RunState, stage, route_role, report_only, configured, *, model=None) -> dict | None:
    """``effort_choice`` decision for a Builder launch under per-task Cascade sizing, or None."""
    if (
        stage != STAGE
        or route_role != STAGE
        or report_only
        or not configured
        or configured not in route_fit.SCALE
        or os.environ.get(SETTING) == "0"
    ):
        return None
    settings = state.get("settings") or {}
    origin = (settings.get("effort_origins") or {}).get(STAGE)
    if origin not in _ORIGINS:
        return None
    route = (settings.get("roles") or {}).get(STAGE) or {}
    if (route.get("reasoning_effort") and route.get("reasoning_effort") != configured) or (
        model and route.get("model") and route.get("model") != model
    ):
        return None
    if any(isinstance(row, dict) and row.get("role") == STAGE for row in state.get("reasoning_escalations") or ()):
        return None
    if _cross_model_escalated(state, route):
        return None
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "approved":
        return None
    task = state.get("current_task") or {}
    paths = task.get("affected_paths") or []
    if not task.get("id") or not paths:
        return None
    saved_model = model or route.get("model")
    offered = _offered(settings, saved_model)
    signals = _hard_signals(settings, paths)
    prior = _prior_attempts(state, task)
    bounded = 1 <= len(paths) <= SMALL_PATHS and not signals

    # Hard task upfront, or retry after an attempt already ran at (or above) the configured ceiling.
    need_climb = bool(signals) or (prior >= 2 if bounded else prior >= 1)
    if need_climb:
        if offered is None:
            return (
                _kept(
                    configured,
                    "levels_unknown",
                    f"the levels {saved_model} offers are not known",
                    saved=configured,
                    signals=signals or ["earlier_failure"],
                )
                if signals and origin == role_route.STAGE_CHOICE
                else None
            )
        higher = _above(offered, configured)
        if higher is None:
            return (
                _kept(
                    configured,
                    "strongest_level",
                    f"{configured} is the strongest level {saved_model} offers",
                    saved=configured,
                    signals=signals or ["earlier_failure"],
                )
                if signals and origin == role_route.STAGE_CHOICE
                else None
            )
        reason = "hard_task" if signals else "retry_climb"
        text = (
            "the build task is hard (" + ", ".join(_SIGNAL_TEXT[s] for s in signals) + ")"
            if signals
            else "an earlier Builder attempt at your saved level did not clear this task"
        )
        return {
            "level": higher,
            "ceiling": higher,
            "lowered": False,
            "raised": True,
            "saved": configured,
            "reason": reason,
            "signals": signals or ["earlier_failure"],
            "text": text,
        }

    # Runner/auto defaults still lower via ``effort()`` without emitting a stage-choice note.
    if origin != role_route.STAGE_CHOICE:
        return None

    if prior >= 1:
        return _kept(configured, "earlier_attempt", "an earlier Builder attempt already ran for this task")
    if not bounded:
        return None
    if offered is None:
        return _kept(configured, "levels_unknown", f"the levels {saved_model} offers are not known")
    lower = _below_offered(offered, configured)
    if lower is None:
        return _kept(configured, "no_lower_level", f"{saved_model} offers no level below {configured}")
    noun = "file" if len(paths) == 1 else "files"
    return {
        "level": lower,
        "ceiling": configured,
        "lowered": True,
        "reason": "bounded_task",
        "text": f"first pass on an approved {len(paths)}-{noun} task",
    }


def _hard_signals(settings: dict, paths: list) -> list[str]:
    found: list[str] = []
    if len(paths) >= HARD_PATHS:
        found.append("wide_task")
    patterns = settings.get(sensitive_paths.SETTING)
    if isinstance(patterns, list) and patterns and sensitive_paths.matched(paths, patterns):
        found.append("sensitive_paths")
    return found


def _prior_attempts(state: RunState, task: dict) -> int:
    task_ids = {task["id"]}
    milestones = set(task.get("milestone_ids") or ([task["milestone_id"]] if task.get("milestone_id") else []))
    if milestones:
        for archived in state.get("task_archive") or ():
            if not isinstance(archived, dict) or not archived.get("id"):
                continue
            archived_milestones = set(
                archived.get("milestone_ids") or ([archived["milestone_id"]] if archived.get("milestone_id") else [])
            )
            if archived_milestones & milestones:
                task_ids.add(archived["id"])
        for batch in state.get("orchestration_history") or ():
            if not isinstance(batch, dict):
                continue
            for worker in batch.get("workers") or ():
                if isinstance(worker, dict) and worker.get("milestone_id") in milestones:
                    worker_task = worker.get("task")
                    if isinstance(worker_task, dict) and worker_task.get("id"):
                        task_ids.add(worker_task["id"])
    for rework in state.get("direct_rework_assignments") or ():
        if isinstance(rework, dict) and rework.get("assigned_task_id") in task_ids and rework.get("source_task_id"):
            task_ids.add(rework["source_task_id"])
    matching = [
        row
        for row in state.get("stages") or ()
        if isinstance(row, dict)
        and not row.get("dry_run")
        and (row.get("task_id") in task_ids or (milestones and row.get("worker_milestone") in milestones))
        and STAGE in (row.get("stage"), row.get("original_stage"), row.get("role"))
    ]
    exec_rows = [
        row
        for row in matching
        if not row.get("report_only") and not str(row.get("stage") or "").endswith("_report_repair")
    ]
    exec_keys = {(row.get("task_id"), row.get("iteration")) for row in exec_rows}
    orphan_repairs = sum(
        1 for row in matching if row not in exec_rows and (row.get("task_id"), row.get("iteration")) not in exec_keys
    )
    recorded = len(exec_rows) + orphan_repairs
    if recorded or not milestones:
        return recorded
    # Isolated parallel worker child runs start with ``stages=[]``; count integrated prior batches for this milestone.
    return sum(
        1
        for batch in state.get("orchestration_history") or ()
        if isinstance(batch, dict)
        for worker in batch.get("workers") or ()
        if isinstance(worker, dict) and worker.get("milestone_id") in milestones and worker.get("status") == "BUILT"
    )


def _cross_model_escalated(state: RunState, route: dict) -> bool:
    current_model = route.get("model")
    for lane in (state.get("builder_retries") or {}).values():
        if not isinstance(lane, dict) or lane.get("action") != "escalate":
            continue
        initial = lane.get("initial_route")
        if isinstance(initial, dict) and initial.get("model") and initial.get("model") != current_model:
            return True
    return False


def _offered(settings: dict, model) -> list[str] | None:
    if not isinstance(model, str):
        return None
    listed = route_fit.declared_for(settings, model)
    if listed is None:
        return None
    return sorted(
        (level for level in set(listed) | set(route_fit.certified_for(settings, model)) if level in route_fit.SCALE),
        key=route_fit.SCALE.index,
    )


def _below_offered(offered: list[str], configured: str) -> str | None:
    rank = route_fit.SCALE.index(configured)
    lower = [level for level in offered if route_fit.SCALE.index(level) < rank]
    return lower[-1] if lower else None


def _above(offered: list[str], configured: str) -> str | None:
    rank = route_fit.SCALE.index(configured)
    return next((level for level in route_fit.SCALE[rank + 1 :] if level in offered), None)


def _kept(configured: str, reason: str, text: str, **extra) -> dict:
    return {
        "level": configured,
        "ceiling": configured,
        "lowered": False,
        "raised": False,
        "reason": reason,
        "text": text,
        **extra,
    }
