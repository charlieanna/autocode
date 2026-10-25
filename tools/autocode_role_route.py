"""The route record: which tool runs a role, on whose account, and why it is there (#1426).

One record per configured role, derived from the run's saved settings and from the events
that moved a route after the start. Nothing here launches a provider, connects an account,
reads a credential or calls a model.

``job`` is the role's primary job on screen (autocode_roles.route_name), and ``provider`` is
the tool that runs it: the role's own provider when it names a real tool, else the run's, else
``opencode``; for a role on the Codex engine, the engine's own name. Both keep the meaning the
status view gave them (#717).

``account`` is the billing segment of the model ID, which is all a saved route can prove:
``zai-coding-plan/glm-5.3`` bills the Z.AI Coding Plan and ``kilo/anthropic/claude-opus-5.5``
bills the Kilo Gateway, while a bare ``gpt-5.6-sol`` bills whichever login the engine itself
holds, so it stays None. Whether that account is connected, and whether it may be billed, is
the model preflight's answer (autocode_model_preflight), never this record's.

``reasoning_effort`` is the level a launch passes; None means the model's own default.
The separate ``settings.effort_origins.<role>`` (every role of a new run) records an
explicit effort choice versus the runner/selector default. Configure owns this map; the
planning policy and the launch-boundary effort fit read it. Unknown legacy effort
provenance never means a default.

``origin`` is why the route is what it is, read from the record that moved it, the newest
cause first: a model named at a quota stop, the Builder's stronger attempt, a ladder climb,
a replacement accepted when the run started, then the origin configure saved when the route
was chosen: a new run (the person named the model, --single-model named it, or the runner's
default table did) or a saved run's own ``--<role>-model``. A role copied into existence
mid-run reads ``derived``, and a run saved before this record existed reads ``unrecorded``
rather than a guess. Selecting routes automatically (#1426) adds its own origin here;
nothing else may.

Pure functions. The quota assignments arrive as an argument because autocode_quota_route
owns what counts as one; this module imports no controller and no provider facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
    from . import autocode_stage_roles as stage_roles
except ImportError:
    import autocode_role_transport as role_transport
    import autocode_roles as roles
    import autocode_stage_roles as stage_roles

# The origins a choice writes when it makes one; the rest (``quota_assigned``,
# ``stronger_attempt``, ``escalated``, ``derived``, ``unrecorded``) are read from the record
# of the change, so no writer names them.
USER_EXPLICIT = "user_explicit"
SINGLE_MODEL = "single_model"
RUNNER_DEFAULT = "runner_default"
CATALOGUE_REPLACEMENT = "catalogue_replacement"
# The automatic selector's own choice (#1426 step 7, autocode_route_selector)
AUTO_SELECTED = "auto_selected"
CONFIGURED_ORIGINS = (USER_EXPLICIT, SINGLE_MODEL, RUNNER_DEFAULT, CATALOGUE_REPLACEMENT, AUTO_SELECTED)
# A flag that also names another role's route, so that route is the person's choice too
# (autocode_configure.configure_joint). tests/test_role_route.py pins the pairing.
ALSO_NAMED_BY = {"requirements": ("glm_model",)}


def account(model):
    """The billing segment of a model ID; None when the engine's own login decides."""
    if not isinstance(model, str) or "/" not in model:
        return None
    return model.split("/", 1)[0] or None


# How a provider prefix bills. OpenAI depends on how OpenCode signs in (see ``plan``).
PLANS = {
    "zai-coding-plan": ("Z.AI Coding Plan", "subscription"),
    "github-copilot": ("GitHub Copilot", "subscription"),
    "zai": ("Z.AI API", "pay per token"),
    "opencode": ("OpenCode Zen", "pay per token"),
    "kilo": ("Kilo Gateway", "pay per token"),
}
BILLING_KINDS = ("subscription", "pay per token")


def plan(model: str, openai_auth: str | None = None) -> tuple[str, str | None]:
    """(plan, billing) for display; authentication never bans a model."""
    provider = model.split("/", 1)[0]
    if provider != "openai":
        return PLANS.get(provider, (provider, None))
    if openai_auth == "oauth":
        return "ChatGPT login", "subscription"
    if openai_auth == "api":
        return "OpenAI via api", "pay per token"
    return "OpenAI", None


def billing(model, settings=None):
    """How a stage's model bills: "subscription", "pay per token", or None when unknown (#1924).

    An automatically selected run saved the billing discovery found for each permitted candidate
    (``route_selection["candidates"]``); otherwise the model ID's account decides. An OpenAI model's
    billing depends on its login, which a saved run does not otherwise record, so it stays None.
    """
    if not isinstance(model, str) or not model:
        return None
    selection = (settings or {}).get("route_selection") if isinstance(settings, dict) else None
    saved = (selection or {}).get("candidates") if isinstance(selection, dict) else None
    for candidates in saved.values() if isinstance(saved, dict) else ():
        for candidate in candidates if isinstance(candidates, list) else ():
            if (
                isinstance(candidate, dict)
                and candidate.get("model") == model
                and candidate.get("billing") in BILLING_KINDS
            ):
                return candidate["billing"]
    return plan(model)[1] if "/" in model else None


def engine(settings, role):
    """The engine that runs ``role`` (autocode_stage_roles.engine_for, accepting no settings)."""
    return stage_roles.engine_for(settings or {}, role)


def provider(settings, role):
    """The tool that runs ``role``; the engine's own name when it is not launched through one."""
    settings = settings or {}
    engine_name = engine(settings, role)
    if engine_name != "opencode":
        return engine_name
    named = ((settings.get("roles") or {}).get(role) or {}).get("provider")
    if named not in (None, "openai", "opencode"):
        return named
    return settings.get("provider") or "opencode"


def origin(settings, role, route, *, assignments=(), escalations=(), retries=()):
    """Why ``role`` runs the route it does, from the record that moved it last."""
    settings = settings or {}
    model = route.get("model")
    effort = route.get("reasoning_effort")
    if any(row.get("role") == role and row.get("to") == model for row in _rows(assignments)):
        return "quota_assigned"
    if (
        role == "terra"
        and model
        and model == (settings.get("builder_retry") or {}).get("strong_model")
        and any(row.get("action") == "escalate" for row in _rows(retries))
    ):
        return "stronger_attempt"
    if any(
        row.get("role") == role
        and (row.get("selected") or {}).get("model") == model
        and (row.get("selected") or {}).get("reasoning_effort") == effort
        for row in _rows(escalations)
    ):
        return "escalated"
    saved = (settings.get("route_origins") or {}).get(role)
    if saved in CONFIGURED_ORIGINS:
        return saved
    if role in role_transport.DERIVED_ROLES:
        return "derived"
    return "unrecorded"


def record(state: RunState, role, *, assignments=(), escalations=(), retries=()):
    """One role's route: its model, engine, job, reasoning level, tool, account and origin."""
    state = state or {}
    settings = state.get("settings") or {}
    route = (settings.get("roles") or {}).get(role)
    route = route if isinstance(route, dict) else {}
    model = route.get("model")
    return {
        "model": model,
        "engine": engine(settings, role),
        "job": roles.route_name(role, state),
        "reasoning_effort": route.get("reasoning_effort"),
        "provider": provider(settings, role),
        "account": account(model),
        "origin": origin(settings, role, route, assignments=assignments, escalations=escalations, retries=retries),
    }


def records(state: RunState, assignments=()) -> dict:
    """Every configured role's record, by role: the routes the next launch uses."""
    settings = (state or {}).get("settings") or {}
    evidence = {
        "assignments": assignments,
        "escalations": state.get("reasoning_escalations") or (),
        "retries": state.get("builder_retry_decisions") or (),
    }
    return {
        role: record(state, role, **evidence)
        for role in sorted(settings.get("roles") or {})
        if isinstance((settings.get("roles") or {}).get(role), dict)
    }


def origins(args, roles, models, single_model) -> dict:
    """The origin of every role of a new run: the person named it, or the runner did.

    ``models`` carries a model an earlier command of this conversation named, which is the
    person's choice too. A saved run is not re-recorded wholesale: only the routes it
    actually re-chooses are (``moved``), so the rest keep the reason they were chosen for.
    """
    result = {}
    for role in roles:
        if single_model:
            result[role] = SINGLE_MODEL
        else:
            result[role] = USER_EXPLICIT if _named(args, role, models) else RUNNER_DEFAULT
    return result


def moved(args, settings) -> None:
    """Record the person's choice for a route this launch of a saved run moved (#1426).

    A resume re-chooses only what a role's own flag names, so only those routes are
    re-recorded; every other role keeps the origin its new run saved, and a run saved before
    this record existed keeps none. A role this resume creates rather than moves stays
    ``unrecorded``: which flag named it is the creating branch's business, not this one's.
    """
    for role in (settings or {}).get("roles") or {}:
        if getattr(args, f"{role}_model", None):
            settings.setdefault("route_origins", {})[role] = USER_EXPLICIT


def _named(args, role, models) -> bool:
    """Whether a person named this role's model, by its own flag or by one it also answers to."""
    return bool(
        getattr(args, f"{role}_model", None)
        or models.get(role)
        # --glm-model names the Requirements route too when --requirements-model does not
        # (autocode_configure.configure_joint).
        or any(getattr(args, name, None) for name in ALSO_NAMED_BY.get(role, ()))
    )


def record_efforts(args, settings, *, fresh):
    """Every configured role's effort provenance, for the launch-boundary fit (#2057).

    A role's own flag names it, the global flag names every role that uses it, and a fresh
    run's remaining roles were the runner's or selector's choice. A saved run keeps unknown
    provenance, so its existing effort is never moved.
    """
    for role in sorted((settings or {}).get("roles") or {}):
        _record_effort(args, settings, role, fresh=fresh)


def _record_effort(args, settings, role, *, fresh):
    route = (settings.get("roles") or {}).get(role)
    if not isinstance(route, dict):
        return
    named = getattr(args, f"{role}_reasoning_effort", None)
    global_effort = getattr(args, "reasoning_effort", None)
    if named is not None or global_effort is not None and route.get("reasoning_effort") == global_effort:
        chosen = USER_EXPLICIT
    elif fresh:
        selected = ((settings.get("route_selection") or {}).get("routes") or {}).get(role) or {}
        chosen = AUTO_SELECTED if selected.get("origin") == AUTO_SELECTED else RUNNER_DEFAULT
    else:
        return
    settings.setdefault("effort_origins", {})[role] = chosen


def reviewer_effort_can_reduce(settings):
    """Only a recorded automatic/default effort may be tiered; unknown saved choices stay."""
    return effort_can_reduce(settings, "plan_reviewer")


def effort_can_reduce(settings, role):
    """True only when the role's effort was recorded as the runner's or selector's, never a person's or unknown."""
    return (settings.get("effort_origins") or {}).get(role) in (RUNNER_DEFAULT, AUTO_SELECTED)


def _rows(values):
    return [row for row in values or () if isinstance(row, dict)]
