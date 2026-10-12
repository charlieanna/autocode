"""The one list of job names a person sees on screen and in docs.

Stage code names (`terra`, `sol`, `astra_checkpoint`, …) stay unchanged: saved runs
and state keys depend on them. This module maps each stage to a *job*, and each job
to the screen name. Name the job being done, not the AI that happens to do it —
when reviewer routing gives the Plan Reviewer the testing job, the screen says
``Tester``.

Do not add a second name table. ``autocode_status.role_name`` is the only reader.
"""

from __future__ import annotations

# The canonical planning stage lists (units/autoplanner.py runs them). Every
# module that asks "is this a planning stage?" asks here; private copies have
# diverged before (issue #1124). tests/test_planning_stages.py enforces it.
PLANNING_STAGES = ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
PLANNING_V2_STAGES = ("requirements", "plan", "plan_review", "plan_revise", "plan_finalize")
ALL_PLANNING_STAGES = PLANNING_STAGES + PLANNING_V2_STAGES


def planning_flow(source: dict) -> str:
    """A run's planning flow: "v2" or "legacy".

    Accepts a run state (its ``settings`` hold the flag) or a settings dict
    itself. Anything other than exactly "v2" is the legacy flow.
    """
    settings = source.get("settings") if "settings" in source else source
    return "v2" if (settings or {}).get("planning_flow") == "v2" else "legacy"


# Job key -> the name printed for a person.
SCREEN = {
    "recognizer": "Job recognizer",
    "requirements": "Requirements",
    "planner": "Planner",
    "plan_reviewer": "Plan Reviewer",
    "builder": "Builder",
    "tester": "Tester",
    "completion": "Completion Reviewer",
    "resolver": "Resolver",
    "design_inventory": "Design inventory",
    "designer": "Designer",
    "design_reviewer": "Design Reviewer",
    "code_reviewer": "Code Reviewer",
    "investigator": "Investigator",
    "analyst": "Analyst",
    "orchestrator": "Orchestrator",
}

# Stage code name -> job key. ``_report_repair`` is stripped before the lookup.
# A stage that can do more than one job is overridden in job_for_stage.
STAGE_JOB = {
    "recognize_workflow": "recognizer",
    "requirements_gather": "requirements",
    "astra_discovery": "requirements",
    "glm_revise": "requirements",
    "requirements_planner": "requirements",
    "requirements_revision": "requirements",
    "requirements": "requirements",
    "astra_plan": "planner",
    "plan": "planner",
    "plan_revise": "planner",
    "astra_challenge": "plan_reviewer",
    "astra_finalize": "plan_reviewer",
    "plan_reviewer": "plan_reviewer",
    "plan_finalizer": "plan_reviewer",
    "plan_review": "plan_reviewer",
    "plan_finalize": "plan_reviewer",
    "orchestrator": "orchestrator",
    "terra": "builder",
    "builder": "builder",
    "sol": "tester",
    "validator": "tester",
    "astra_review": "completion",
    "decision_owner": "completion",
    "astra_checkpoint": "completion",
    "astra_resolve": "resolver",
    "astra_diagnose": "resolver",
    "collect_design": "design_inventory",
    "review_design": "designer",
    "check_design": "design_reviewer",
    "review_change": "code_reviewer",
    "investigate_bug": "investigator",
    "investigate_stuck": "investigator",
    "answer_question": "analyst",
}

# Reviewer-routing modes that hand independent validation to the Plan Reviewer
# (autocode_workflow.MODE). Their astra_checkpoint stage runs the Tester's job.
# Keep in sync with autocode_workflow.MODE — tested in tests/test_status_updates.py.
VALIDATION_ROUTING_MODES = ("glm_first_v1",)


def base_stage(stage: str) -> str:
    return str(stage or "").removesuffix("_report_repair")


def job_for_stage(stage: str, state: dict | None = None) -> str | None:
    """Job key for a stage code name, or None when the stage is unknown."""
    key = base_stage(stage)
    job = STAGE_JOB.get(key)
    if job is None:
        return None
    if key == "astra_checkpoint" and state is not None:
        mode = ((state.get("settings") or {}).get("workflow") or {}).get("mode")
        if mode in VALIDATION_ROUTING_MODES:
            return "tester"
    return job


def screen_name(stage: str, state: dict | None = None) -> str:
    """Printed name for a stage: 'terra' -> 'Builder'. Unknown stages title-case."""
    if not stage:
        return ""
    job = job_for_stage(stage, state)
    if job is not None:
        return SCREEN[job]
    return base_stage(stage).replace("_", " ").title()
