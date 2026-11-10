"""Browser presentation adapter over the canonical autocode_roles catalogue.

Names and mode-dependent job identity come only from autocode_roles. This adapter
adds activity descriptions and legacy presentation aliases, never model routes.
"""

try:
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles

from typing import Any

ACTIVITIES = {
    "requirements_gather": "Gathering requirements",
    "requirements": "Gathering requirements",
    "requirements_planner": "Gathering requirements",
    "draft_plan": "Planning",
    "plan": "Planning",
    "revise_plan": "Revising the plan",
    "plan_revise": "Revising the plan",
    "requirements_revision": "Revising the plan",
    "plan_task": "Assigning implementation",
    "review_plan": "Challenging the plan",
    "plan_review": "Challenging the plan",
    "plan_reviewer": "Challenging the plan",
    "finalize_plan": "Finalizing the plan",
    "plan_finalize": "Finalizing the plan",
    "plan_finalizer": "Finalizing the plan",
    "builder": "Implementing",
    "tester": "Reviewing implementation",
    "validator": "Reviewing implementation",
    "review_completion": "Deciding complete or rework",
    "decision_owner": "Deciding complete or rework",
    "review_checkpoint": "Auditing results",
    "resolve": "Diagnosing a failure",
    "diagnose": "Diagnosing a failure",
    "resolver": "Runner decision (no model call)",
    "orchestrator": "Coordinating Builders",
    "investigate_stuck": "Investigating",
    "check_design": "Checking the design",
}
ALIASES = {
    "planner": "planner",
    "task_planner": "planner",
    "builder": "builder",
    "tester": "tester",
    "completion owner": "completion",
    "plan reviewer": "plan_reviewer",
    "requirements gatherer": "requirements",
    "requirements planner": "requirements",
    "autoresolver": "resolver",
    "validator": "tester",
    "completion reviewer": "completion",
    "architect": "design_reviewer",
}
# Actual V2 stages are in the canonical catalogue; only legacy aliases belong here.
STAGE_ALIASES = {"diagnose": "resolve", "resolver": "resolve"}


def role_name(stage, workflow_mode=None):
    base = roles.base_stage(stage)
    return roles.screen_name(STAGE_ALIASES.get(base, base), {"settings": {"workflow": {"mode": workflow_mode}}})


def role_label(role):
    key = str(role).lower().replace("_", " ")
    job = ALIASES.get(key, role)
    return roles.SCREEN.get(job, str(role).replace("_", " ").title())


CATALOGUE: dict[str, Any] = {
    "roles": {
        **roles.SCREEN,
        "validator": roles.SCREEN["tester"],
        "architect": roles.SCREEN["design_reviewer"],
    },
    "aliases": ALIASES,
    "stages": {
        stage: {"role": role_name(stage), "activity": ACTIVITIES.get(stage, "Working")}
        for stage in set(roles.STAGE_JOB) | set(STAGE_ALIASES)
    },
    "modes": {
        mode: {
            "review_checkpoint": {
                "role": role_name("review_checkpoint", mode),
                "activity": "Independent validation"
                if mode in roles.VALIDATION_ROUTING_MODES
                else "Final completion audit",
            }
        }
        for mode in (*roles.VALIDATION_ROUTING_MODES, "glm_final_audit_v2")
    },
}
ROLES = {stage: row["role"] for stage, row in CATALOGUE["stages"].items()}
