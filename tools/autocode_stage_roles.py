"""Which stages plan on a run, which role runs a stage, and which engine a role runs on.

Pure functions of a run's saved settings. The planning unit (units/autoplanner) routes and prepares
its stages with them and re-exports them, so ``autocode_planning.role_for`` and the others are the
same functions as before. The stage context (autocode_stage_context) reads them from here: it used to
import the planning unit for them, and the planning unit imports the stage context, which closed an
import cycle. ``enabled`` is whether the run plans jointly (``settings.joint_planning``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as role_names
except ImportError:
    import autocode_roles as role_names

STAGES = role_names.PLANNING_STAGES
V2_STAGES = role_names.PLANNING_V2_STAGES
V2_STAGE_ROLES = {
    "requirements": "requirements",
    "plan": "glm",
    "plan_revise": "glm",
    "plan_review": "plan_reviewer",
    "plan_finalize": "plan_reviewer",
}


def enabled(state: RunState):
    return bool(state.get("settings", {}).get("joint_planning"))


def is_planning(state: RunState, stage):
    stages = V2_STAGES if role_names.planning_flow(state) == "v2" else STAGES
    return enabled(state) and stage in stages


def role_for(state: RunState, stage):
    if role_names.planning_flow(state) == "v2" and stage in V2_STAGE_ROLES:
        return V2_STAGE_ROLES[stage]
    if is_planning(state, stage) and stage == "requirements_gather":
        return "requirements"
    if is_planning(state, stage) and stage in ("astra_discovery", "glm_revise"):
        return "glm"
    return "astra" if stage.startswith("astra") else stage


def engine_for(settings, role):
    return settings.get("roles", {}).get(role, {}).get("engine", settings.get("engine", "codex"))
