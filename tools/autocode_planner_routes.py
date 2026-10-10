"""Default continuous-conversation model profile.

This policy applies to conversations using this profile. It does not replace
AutoCode's global defaults or modify routes saved by unrelated task runs.
The module is pure domain logic and never imports the dashboard or controller.
"""

from __future__ import annotations

from copy import deepcopy

SOL_PLANNER_MODEL = "openai/gpt-6-sol"
GLM_REVIEW_MODEL = "zai-coding-plan/glm-5.3"
ASTRA_VISUAL_MODEL = "openai/gpt-6-astra"


class PlannerDispatchError(ValueError):
    """A Planner dispatch or structured result failed; safe to display, evidence retained."""

    def __init__(self, message, *, stage="planner", evidence=None):
        super().__init__(message)
        self.stage = stage
        self.evidence = evidence


class PlannerRouteError(PlannerDispatchError):
    """The configured routes are malformed or violate verifier independence."""

    def __init__(self, message):
        super().__init__(message, stage="route_policy")


def _route(model, effort):
    return {"engine": "opencode", "provider": "opencode", "model": model, "reasoning_effort": effort}


# Historical public name retained for compatibility. These routes are defaults;
# explicit model and reasoning selections override them.
MANDATED_ROUTES = {
    "requirements_gatherer": _route(SOL_PLANNER_MODEL, "low"),
    "planner": _route(SOL_PLANNER_MODEL, "high"),
    "builder": _route(SOL_PLANNER_MODEL, "high"),
    "resolver": _route(SOL_PLANNER_MODEL, "max"),
    "architect": _route(GLM_REVIEW_MODEL, "high"),
    "plan_reviewer": _route(GLM_REVIEW_MODEL, "high"),
    "validation": _route(GLM_REVIEW_MODEL, "high"),
    "completion": _route(GLM_REVIEW_MODEL, "high"),
    "visual_review": _route(ASTRA_VISUAL_MODEL, "high"),
}
POLICY_ROLES = (
    "planner",
    "builder",
    "resolver",
    "architect",
    "plan_reviewer",
    "validation",
    "completion",
    "visual_review",
)
VERIFIER_ROLES = ("architect", "plan_reviewer", "validation", "completion")
NONVISUAL_ROLES = (
    "requirements_gatherer",
    "planner",
    "builder",
    "resolver",
    "architect",
    "plan_reviewer",
    "validation",
    "completion",
)

# Usage/time/idle/tool/iteration caps are disabled (None/0 = unlimited, matching
# the runner's execution-limits convention) while semantic safety checks are
# retained: the deny-all tool-free Planner session, route validation
# and cross-model verifier separation below.
DISPATCH_LIMITS = {
    "usage_cap": None,
    "time_cap_seconds": 0,
    "idle_timeout_seconds": 0,
    "iteration_ceiling": None,
    "tool_policy": "deny-all",
    "semantic_safety": ("deny_all_tools", "route_policy", "cross_model_verification"),
}


def caps_disabled():
    """The Planner dispatch cap configuration: everything disabled, safety kept."""
    return dict(DISPATCH_LIMITS)


def conversation_planner_routes():
    """The routes this pipeline adds to a conversation's configured_routes."""
    return {"planner": deepcopy(MANDATED_ROUTES["planner"])}


def visual_review_route():
    """Default visual-review route; selecting it does not establish visual evidence."""
    return deepcopy(MANDATED_ROUTES["visual_review"])


def select_visual_review_route(route=None):
    """Validate an explicit visual route, or use the default when omitted."""
    candidate = visual_review_route() if route is None else route
    return enforce_route_policy({"visual_review": candidate})["visual_review"]


# Fresh runner-configuration boundary: runner role names mapped to their
# shared-policy roles.  Saved runs keep their persisted pins as immutable
# history; only newly constructed role settings (a new run or the fresh half
# of a joint configuration) pass through the fresh-configuration check, and
# custom providers keep their own catalogues.
RUNNER_NONVISUAL_ROLES = ("requirements", "glm", "plan_reviewer", "astra", "terra", "sol", "completion", "resolver")
RUNNER_POLICY_ROLES = {
    "requirements": "requirements_gatherer",
    "glm": "planner",
    "plan_reviewer": "plan_reviewer",
    "astra": "architect",
    "terra": "builder",
    "sol": "validation",
    "completion": "completion",
    "resolver": "resolver",
}


def enforce_fresh_runner_role_models(models, efforts=None, *, complete=False):
    """Validate fresh role names and model values without pinning model IDs."""
    models, efforts = models or {}, efforts or {}
    unknown = (set(models) | set(efforts)) - set(RUNNER_POLICY_ROLES) - {"visual_review"}
    if unknown:
        raise PlannerRouteError("Unknown fresh runner route(s): " + ", ".join(sorted(unknown)))
    if complete and set(RUNNER_POLICY_ROLES) - set(models):
        raise PlannerRouteError(
            "Missing fresh runner route(s): " + ", ".join(sorted(set(RUNNER_POLICY_ROLES) - set(models)))
        )
    for role, route in models.items():
        model = route.get("model") if isinstance(route, dict) else route
        _validate_model(model, role)
    return True


def _validate_model(model, role):
    if not isinstance(model, str) or not model or any(c.isspace() for c in model):
        raise PlannerRouteError(f"The {role} route needs a model without whitespace.")


def _model_family(model):
    """Producer family for cross-verification, mirroring autocode_dispatch.

    Kept local so this backend module stays import-light and offline-testable
    (autocode_dispatch pulls runner process dependencies).
    """
    if not isinstance(model, str) or not model:
        return ""
    name = model.rsplit("/", 1)[-1].lower()
    if model.startswith("zai-coding-plan/") or name.startswith("glm-"):
        return "glm"
    if model.startswith("xiaomi-token-plan-sgp/") or name.startswith("mimo-"):
        return "mimo"
    return model


def enforce_route_policy(routes):
    """Validate represented routes and independence; preserve explicit selections."""
    if not isinstance(routes, dict) or not routes:
        raise PlannerRouteError("Planner pipeline routes must be a non-empty object.")
    unknown = sorted(set(routes) - set(MANDATED_ROUTES))
    if unknown:
        raise PlannerRouteError("Unknown Planner pipeline route(s): " + ", ".join(unknown))
    for name, route in routes.items():
        if not isinstance(route, dict):
            raise PlannerRouteError(f"The {name} route must be an object.")
        _validate_model(route.get("model"), name)
    if any(role in routes for role in NONVISUAL_ROLES) and "planner" not in routes:
        raise PlannerRouteError("The independent Planner route is required and must not be dropped.")
    if "planner" in routes:
        planner_family = _model_family(routes["planner"]["model"])
        for verifier in VERIFIER_ROLES:
            if verifier in routes and _model_family(routes[verifier]["model"]) == planner_family:
                raise PlannerRouteError(
                    f"{verifier} must not grade Planner work: both use the {planner_family} family. "
                    "Cross-model verifier separation is required."
                )
    return deepcopy(routes)


def enforce_conversation_routes(routes):
    """Validate routes for new dispatch, including a configured Gatherer."""
    enforced = enforce_route_policy(routes)
    if "requirements_gatherer" not in enforced:
        raise PlannerRouteError("The Requirements Gatherer route is required for new dispatch.")
    return enforced


def configure_runner_profile(settings, args):
    """Select the continuous profile for a new conversation handoff only.

    Explicit routes are checked before mutation. Existing runs never call this
    helper. Normal CLI tasks retain the provider defaults selected elsewhere.
    Explicit user budgets remain authoritative; this profile disables defaults.
    """
    if settings.get("engine") != "opencode" or settings.get("provider") != "opencode":
        raise PlannerRouteError("Continuous conversation handoff requires the built-in OpenCode provider.")
    models = {
        role: getattr(args, role + "_model", None)
        for role in RUNNER_POLICY_ROLES
        if getattr(args, role + "_model", None) is not None
    }
    efforts = {
        role: getattr(args, role + "_reasoning_effort", None)
        if getattr(args, role + "_reasoning_effort", None) is not None
        else getattr(args, "reasoning_effort", None)
        for role in RUNNER_POLICY_ROLES
        if getattr(args, role + "_reasoning_effort", None) is not None
        or getattr(args, "reasoning_effort", None) is not None
    }
    enforce_fresh_runner_role_models(models, efforts)
    strong = getattr(args, "builder_strong_model", None)
    if strong is not None:
        enforce_fresh_runner_role_models({"terra": strong})
    configured = deepcopy(settings)
    for role, policy_role in RUNNER_POLICY_ROLES.items():
        prior = configured.setdefault("roles", {}).get(role, {})
        selected = {**prior, **deepcopy(MANDATED_ROUTES[policy_role]), "provider": None}
        if role in models:
            model = models[role]
            selected["model"] = "openai/" + model if "/" not in model and model.startswith("gpt-") else model
        if role in efforts:
            selected["reasoning_effort"] = efforts[role]
        configured["roles"][role] = selected
    configured["roles"]["plan_reviewer"]["model_pinned"] = True
    configured["conversation_profile"] = "continuous-v1"
    configured.setdefault("builder_retry", {}).update(
        strong_model=strong or configured["roles"]["terra"]["model"],
        strong_reasoning_effort=configured["roles"]["terra"]["reasoning_effort"],
    )
    limits = configured.setdefault("limits", {})
    for argument, field, value in (
        ("max_seconds", "max_seconds", 0),
        ("max_stage_seconds", "stage_timeout_seconds", 0),
        ("max_idle_seconds", "idle_timeout_seconds", 0),
        ("max_tool_seconds", "tool_timeout_seconds", 0),
        ("no_progress_limit", "no_progress_batches", 0),
    ):
        if getattr(args, argument, None) is None:
            limits[field] = value
    if getattr(args, "max_iterations", None) is None and getattr(args, "legacy_iteration_ceiling", None) is None:
        limits["iteration_ceiling"] = None
    checkpoints = configured.setdefault("milestone_checkpoints", {})
    if getattr(args, "max_milestone_seconds", None) is None:
        checkpoints["max_seconds"] = 0
    if getattr(args, "max_milestone_replans", None) is None:
        checkpoints["max_replans"] = None
    enforce_fresh_runner_role_models(configured["roles"], complete=True)
    settings.clear()
    settings.update(configured)
    return settings
