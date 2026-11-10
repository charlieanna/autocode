"""The one list of job names a person sees on screen and in docs.

Stage code names (`terra`, `sol`, `astra_checkpoint`, …) stay unchanged: saved runs
and state keys depend on them. This module maps each stage to a *job*, and each job
to the screen name. Name the job being done, not the AI that happens to do it —
when reviewer routing gives the Plan Reviewer the testing job, the screen says
``Tester``.

Do not add a second name table. Status and client projections read this catalogue.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The canonical planning stage lists (units/autoplanner.py runs them). Every
# module that asks "is this a planning stage?" asks here; private copies have
# diverged before (issue #1124). tests/test_planning_stages.py enforces it.
PLANNING_STAGES = ("requirements_gather", "draft_plan", "review_plan", "revise_plan", "finalize_plan")
PLANNING_V2_STAGES = ("requirements", "plan", "plan_review", "plan_revise", "plan_finalize")
ALL_PLANNING_STAGES = PLANNING_STAGES + PLANNING_V2_STAGES


def planning_flow(source: Mapping[str, Any]) -> str:
    """A run's planning flow: "v2" or "legacy".

    Accepts a run state (its ``settings`` hold the flag) or a settings dict
    itself. Anything other than exactly "v2" is the legacy flow.
    """
    settings = source.get("settings") if "settings" in source else source
    return "v2" if (settings or {}).get("planning_flow") == "v2" else "legacy"


# Job key -> the name printed for a person.
SCREEN = {
    "requirements": "Requirements",
    "planner": "Planner",
    "plan_reviewer": "Plan Reviewer",
    "builder": "Builder",
    "tester": "Tester",
    "completion": "Completion Reviewer",
    "resolver": "Resolver",
    "design_inventory": "Design inventory",
    "design_reviewer": "Design Reviewer",
    "investigator": "Investigator",
    "orchestrator": "Orchestrator",
    # Jobs AutoCode no longer runs (every task takes the same path, #3010): a run an earlier runtime
    # saved still names them in its stages and reports, and is refused at them (autocode_workflows).
    "recognizer": "Job recognizer",
    "designer": "Designer",
    "code_reviewer": "Code Reviewer",
    "analyst": "Analyst",
}

# Stage code name -> job key. ``_report_repair`` is stripped before the lookup.
# A stage that can do more than one job is overridden in job_for_stage.
STAGE_JOB = {
    "requirements_gather": "requirements",
    "draft_plan": "planner",
    "revise_plan": "planner",
    "requirements_planner": "requirements",
    "requirements_revision": "requirements",
    "requirements": "requirements",
    "plan_task": "planner",
    "plan": "planner",
    "plan_revise": "planner",
    "review_plan": "plan_reviewer",
    "finalize_plan": "plan_reviewer",
    "plan_reviewer": "plan_reviewer",
    "plan_finalizer": "plan_reviewer",
    "plan_review": "plan_reviewer",
    "plan_finalize": "plan_reviewer",
    "orchestrator": "orchestrator",
    "builder": "builder",
    "tester": "tester",
    "write_acceptance_tests": "tester",
    "validator": "tester",
    "review_completion": "completion",
    "decision_owner": "completion",
    "review_checkpoint": "completion",
    "resolve": "resolver",
    "diagnose": "resolver",
    "collect_design": "design_inventory",
    "check_design": "design_reviewer",
    "investigate_stuck": "investigator",
    # Retired stages, for a saved run's history (autocode_workflows.RETIRED_STAGES).
    "recognize_workflow": "recognizer",
    "review_design": "designer",
    "review_change": "code_reviewer",
    "investigate_bug": "investigator",
    "answer_question": "analyst",
}

# Configured model slots are distinct from recorded stages: a slot can serve
# several stages. These name its primary job, not a prediction of execution.
ROUTE_JOB = {
    "task_planner": "planner",
    "planner": "planner",
    "requirements": "requirements",
    "plan_reviewer": "plan_reviewer",
    "builder": "builder",
    "tester": "tester",
    "completion": "completion",
    "resolver": "resolver",
    "architect": "design_reviewer",
    "stuck_investigator": "investigator",
    "acceptance_tester": "tester",
}


# The configured slot each job of the standard pipeline runs on, preferred first: the first one a run
# configures is the one its stages launch on (units/autoplanner._resolve_route). Without its own slot,
# the Plan Reviewer and the Completion Reviewer run on ``astra``, and so does the Planner of a run that
# does not plan jointly. A client that shows a job's model reads it through route_for_job, never a
# private table: one pointed the Plan Reviewer at the Resolver's ``astra`` slot (#3087).
PIPELINE = ("requirements", "planner", "plan_reviewer", "builder", "tester", "completion")
JOB_ROUTES = {
    "requirements": ("requirements",),
    "planner": ("planner", "task_planner"),
    "plan_reviewer": ("plan_reviewer", "task_planner"),
    "builder": ("builder",),
    "tester": ("tester",),
    "completion": ("completion", "task_planner"),
}


def route_for_job(job: str, configured: Mapping[str, Any]) -> str | None:
    """The slot ``job`` runs on among the ``configured`` slots (a run's routes), or None."""
    return next((slot for slot in JOB_ROUTES.get(job, ()) if slot in configured), None)


def route_name(role: str, state: Mapping[str, Any]) -> str:
    """Primary job of a configured slot; actual stage names use screen_name.

    ``state`` stays in the call contract (autocode_role_route.record passes it);
    the label no longer reads it: the same Planner work runs the slot in both flows.
    """
    job = ROUTE_JOB.get(role)
    return SCREEN[job] if job else str(role).replace("_", " ").title()


# Reviewer-routing modes that hand independent validation to the Plan Reviewer
# (autocode_workflow.MODE). Their astra_checkpoint stage runs the Tester's job.
# Keep in sync with autocode_workflow.MODE — tested in tests/test_status_updates.py.
VALIDATION_ROUTING_MODES = ("glm_first_v1",)


def base_stage(stage: str) -> str:
    return str(stage or "").removesuffix("_report_repair")


def job_for_stage(stage: str, state: Mapping[str, Any] | None = None) -> str | None:
    """Job key for a stage code name, or None when the stage is unknown."""
    key = base_stage(stage)
    job = STAGE_JOB.get(key)
    if job is None:
        return None
    if key == "review_checkpoint" and state is not None:
        mode = ((state.get("settings") or {}).get("workflow") or {}).get("mode")
        if mode in VALIDATION_ROUTING_MODES:
            return "tester"
    return job


def screen_name(stage: str, state: Mapping[str, Any] | None = None) -> str:
    """Printed name for a stage: 'builder' -> 'Builder'. Unknown stages title-case."""
    if not stage:
        return ""
    job = job_for_stage(stage, state)
    if job is not None:
        return SCREEN[job]
    return base_stage(stage).replace("_", " ").title()
