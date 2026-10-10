"""Browser presentation adapter over the canonical autocode_roles catalogue.

Names and mode-dependent job identity come only from autocode_roles. This adapter
adds activity descriptions and legacy presentation aliases, never model routes.
"""

try:
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles

ACTIVITIES = {
    "requirements_gather": "Gathering requirements",
    "requirements": "Gathering requirements",
    "requirements_planner": "Gathering requirements",
    "astra_discovery": "Planning",
    "plan": "Planning",
    "glm_revise": "Revising the plan",
    "plan_revise": "Revising the plan",
    "requirements_revision": "Revising the plan",
    "astra_plan": "Assigning implementation",
    "astra_challenge": "Challenging the plan",
    "plan_review": "Challenging the plan",
    "plan_reviewer": "Challenging the plan",
    "astra_finalize": "Finalizing the plan",
    "plan_finalize": "Finalizing the plan",
    "plan_finalizer": "Finalizing the plan",
    "terra": "Implementing",
    "builder": "Implementing",
    "sol": "Reviewing implementation",
    "validator": "Reviewing implementation",
    "astra_review": "Deciding complete or rework",
    "decision_owner": "Deciding complete or rework",
    "astra_checkpoint": "Auditing results",
    "astra_resolve": "Diagnosing a failure",
    "astra_diagnose": "Diagnosing a failure",
    "resolver": "Runner decision (no model call)",
    "orchestrator": "Coordinating Builders",
    "recognize_workflow": "Recognizing the job",
    "review_change": "Reviewing changes",
    "investigate_bug": "Investigating",
    "investigate_stuck": "Investigating",
    "review_design": "Checking the design",
    "check_design": "Checking the design",
    "answer_question": "Answering the question",
}
ALIASES = {
    "glm": "planner",
    "astra": "planner",
    "terra": "builder",
    "sol": "tester",
    "completion owner": "completion",
    "plan reviewer": "plan_reviewer",
    "requirements gatherer": "requirements",
    "requirements planner": "requirements",
    "autoresolver": "resolver",
    "validator": "tester",
    "completion reviewer": "completion",
    "reviewer": "code_reviewer",
    "architect": "designer",
}
# Actual V2 stages are in the canonical catalogue; only legacy aliases belong here.
STAGE_ALIASES = {"astra_diagnose": "astra_resolve", "resolver": "astra_resolve"}


def role_name(stage, workflow_mode=None):
    base = roles.base_stage(stage)
    return roles.screen_name(STAGE_ALIASES.get(base, base), {"settings": {"workflow": {"mode": workflow_mode}}})


def role_label(role):
    key = str(role).lower().replace("_", " ")
    job = ALIASES.get(key, role)
    return roles.SCREEN.get(job, str(role).replace("_", " ").title())


CATALOGUE = {
    "roles": {
        **roles.SCREEN,
        "validator": roles.SCREEN["tester"],
        "reviewer": roles.SCREEN["code_reviewer"],
        "architect": roles.SCREEN["designer"],
    },
    "aliases": ALIASES,
    "stages": {
        stage: {"role": role_name(stage), "activity": ACTIVITIES.get(stage, "Working")}
        for stage in set(roles.STAGE_JOB) | set(STAGE_ALIASES)
    },
    "modes": {
        mode: {
            "astra_checkpoint": {
                "role": role_name("astra_checkpoint", mode),
                "activity": "Independent validation"
                if mode in roles.VALIDATION_ROUTING_MODES
                else "Final completion audit",
            }
        }
        for mode in (*roles.VALIDATION_ROUTING_MODES, "glm_final_audit_v2")
    },
}
ROLES = {stage: row["role"] for stage, row in CATALOGUE["stages"].items()}
