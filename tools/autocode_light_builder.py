"""A small approved plan's first Builder attempt runs at low reasoning effort (#1710).

Owner decision, 2026-10-20 (option b). After plan approval the thinking is done: Requirements, the
Planner and the Plan Reviewer worked out what to build and the person approved it. In the #1710 run the
Builder (MiMo v2.6 Pro, medium) still spent 22.6k reasoning tokens, about 555 of its 786 s, on a
one-file ``peek()``; one step deliberated 212 s before a single edit.

The safety net is this module's own, because the effort ladder (autocode_route_ladder) has no rungs for
most routes: any earlier Builder attempt at the same task (a rejected report, a failed check, a
Tester's send-back) means the next one runs at the configured effort. The Tester, the Completion
Reviewer and every planning role keep their configured effort. ``AUTOCODE_LIGHT_BUILDER=0`` turns it off.
An effort the person chose (``--terra-reasoning-effort``, ``--reasoning-effort`` or a project setting), and a
saved run that recorded no provenance, keep it: only the runner's or selector's own default is lowered.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_role_route as role_route
except ImportError:
    import autocode_role_route as role_route

STAGE = "terra"
LOW = "low"
SMALL_PATHS = 4  # files the current task may touch and still count as small
SETTING = "AUTOCODE_LIGHT_BUILDER"
_ABOVE_LOW = ("medium", "high", "xhigh", "max")


def effort(state: RunState, stage, report_only, configured):
    """The reasoning effort for this launch: low for a small task's first Builder attempt, else ``configured``."""
    return LOW if applies(state, stage, report_only, configured) else configured


def applies(state: RunState, stage, report_only, configured) -> bool:
    if stage != STAGE or report_only or configured not in _ABOVE_LOW or os.environ.get(SETTING) == "0":
        return False
    if not role_route.effort_can_reduce(state.get("settings") or {}, STAGE):
        return False
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "approved":
        return False
    body = contract.get("body") or {}
    task = state.get("current_task") or {}
    paths = task.get("affected_paths") or []
    if len(body.get("milestones") or []) > 1 or not task.get("id") or not paths or len(paths) > SMALL_PATHS:
        return False
    # Any earlier attempt at this task, whatever became of it, gets the configured effort back.
    return not any(
        row.get("task_id") == task["id"] and STAGE in (row.get("stage"), row.get("original_stage"), row.get("role"))
        for row in state.get("stages") or []
    )
