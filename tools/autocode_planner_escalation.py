"""The Planner's one stronger attempt: a revised plan the Plan Reviewer still blocks (owner decision 2026-10-23).

Pure domain logic over the run state; imports only autocode_model_identity,
autocode_route_permission and autocode_verifier_independence.

The trigger is substance, not format. In an adaptive run the Plan Reviewer's first challenge
with a blocking concern sends the draft back for revision; when a later challenge of the same
planning cycle blocks the revised draft again, the Planner (the ``glm`` route) moves once onto a
stronger model for the next revision (units.autoplanner.after_challenge). Schema errors,
silences and output caps never trigger it: the existing report repair, recovery and route
handling answer those, and a stronger model would not.

The climb is recorded like an effort climb, in ``reasoning_escalations`` with trigger
``plan_sent_back``, and the route stays where it climbed for the rest of the run, so one run
climbs at most once. If the stronger revision is still blocked, the final review returns the
open concerns to the person as blocking questions, as before.

A second challenge only runs where the review allowance leaves room for it
(autocode_adaptive_planning.after_revise): a large plan, an unlimited allowance, or one the
person raised. Under the default allowance of two review calls the first revision goes straight
to the final review, so the climb never fires there.

Targets, strongest preferred first: Claude Opus 5.5, then GPT-6 Sol, both at high effort. GPT-6
Astra is never a target (it is the Resolver's alone, user 2026-09-28), and neither is Fable 5.1,
which no provider route lists. A target is taken only when it is a different model from the
Planner's, the run's provider serves it (``settings["route_ladders"]["planner"]``, filtered at
launch like the effort ladders), the run's saved accounts permit it, and it does not share a
family with the Plan Reviewer (autocode_verifier_independence).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_route_permission as permission
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_route_permission as permission
    import autocode_verifier_independence as independence

ROLE = "glm"
TRIGGER = "plan_sent_back"
# (model, effort, label). The bare Opus spelling is the built-in Claude provider's; the others are
# OpenCode's. A run keeps only the rungs its provider spells (``rungs``) and serves (``serve``).
TARGETS = (
    ("anthropic/claude-opus-5-5", "high", "Claude Opus 5.5 High"),
    ("claude-opus-5-5", "high", "Claude Opus 5.5 High"),
    ("openai/gpt-6-sol", "high", "GPT-6 Sol High"),
)
# The challenge of one planning cycle whose blocking concerns trigger the climb: the review of a
# draft the Planner already revised once.
CHALLENGE = 2

# Why ``decide`` keeps the Planner on its route.
STAYS = {
    "fixed_profile": "the continuous-v1 conversation profile fixes every role's model and effort",
    "climbed": "the Planner already moved to a stronger model in this run",
    "no_route": "the run has no Planner route",
    "pinned": "a person pinned the Planner's model",
    "other_provider": "the Planner runs on a custom provider, which AutoCode never changes",
    "engine": "the Planner runs on the Codex engine, which reaches no stronger planning model",
    "already_strong": "the Planner already runs on a stronger-attempt model",
    "no_target": "no stronger model is served, permitted and independent of the Plan Reviewer",
}


def _offered(rungs, available):
    """The rungs a model list offers, each spelled as the list spells it: one model behind a gateway
    (``kilo/anthropic/claude-opus-5-5``) is the rung's model (autocode_model_identity.identity)."""
    if available is None:
        return [list(rung) for rung in rungs]
    available = sorted(model for model in available if isinstance(model, str))
    kept = []
    for model, effort, label in rungs:
        spelled = model if model in available else _same_model(model, available)
        if spelled:
            kept.append([spelled, effort, label])
    return kept


def _same_model(model, available):
    wanted = model_identity.identity(model)
    return next((other for other in available if "/" in other and model_identity.identity(other) == wanted), None)


def configure(listed_models=None):
    """A new run's persisted planner rungs, filtered by the provider's model list when it publishes one."""
    return _offered(TARGETS, listed_models)


def serve(settings, available):
    """Keep the planner rungs an OpenCode provider offers, as route_ladder.serve does for effort rungs."""
    ladders = settings.get("route_ladders") or {}
    planner = (settings.get("roles") or {}).get(ROLE)
    if (
        available is None
        or "planner" not in ladders
        or not isinstance(planner, dict)
        or _engine(settings) != "opencode"
    ):
        return settings
    ladders["planner"] = _offered(ladders["planner"], available)
    return settings


def _permitted(settings, model, effort):
    """The rung's model as the run's saved accounts spell it, or None when they do not permit it."""
    if permission.allows(settings, ROLE, model, effort):
        return model
    selection = (settings or {}).get("route_selection") or {}
    saved = [row.get("model") for rows in (selection.get("candidates") or {}).values() for row in rows]
    spelled = _same_model(model, [other for other in saved if isinstance(other, str)])
    return spelled if spelled and permission.allows(settings, ROLE, spelled, effort) else None


def rungs(settings):
    """The run's planner rungs that its provider spells: bare model names only on the built-in Claude provider."""
    claude = (settings or {}).get("provider") == "claude"
    saved = ((settings or {}).get("route_ladders") or {}).get("planner") or ()
    return [tuple(rung) for rung in saved if ("/" not in rung[0]) == claude]


def _engine(settings):
    route = (settings.get("roles") or {}).get(ROLE) or {}
    return route.get("engine") or settings.get("engine") or "codex"


def decide(settings, escalations=()):
    """The stronger rung the Planner moves to, or why it stays.

    Returns ``{"model", "reasoning_effort", "profile"}`` or ``{"stay": reason}`` with a reason from STAYS.
    """
    settings = settings or {}
    if settings.get("conversation_profile") == "continuous-v1":
        return {"stay": "fixed_profile"}
    if any(isinstance(event, dict) and event.get("role") == ROLE for event in escalations or ()):
        return {"stay": "climbed"}
    roles = settings.get("roles") or {}
    route = roles.get(ROLE)
    if not isinstance(route, dict) or not route.get("model"):
        return {"stay": "no_route"}
    if route.get("model_pinned"):
        return {"stay": "pinned"}
    if route.get("provider") not in (None, "openai"):
        return {"stay": "other_provider"}
    if _engine(settings) == "codex":
        return {"stay": "engine"}
    current = model_identity.identity(route["model"])
    candidates = rungs(settings)
    if any(model_identity.identity(model) == current for model, _effort, _label in TARGETS):
        return {"stay": "already_strong"}
    reviewer = (roles.get("plan_reviewer") or {}).get("model")
    vendor = independence.same_vendor_engine(settings)
    for model, effort, label in candidates:
        model = _permitted(settings, model, effort)
        if model is None:
            continue
        if reviewer and independence.shares(model, reviewer, vendor_engine=vendor):
            continue
        return {"model": model, "reasoning_effort": effort, "profile": label}
    return {"stay": "no_target"}


def escalate(state: RunState, *, detail="", struggle_id=None):
    """Move the Planner onto ``decide``'s rung and rotate its session. Returns the escalation event, or
    ``{"stay": reason}`` when the Planner stays where it is."""
    settings = state.get("settings") or {}
    climb = decide(settings, state.get("reasoning_escalations") or ())
    if "stay" in climb:
        return climb
    route = settings["roles"][ROLE]
    previous = {"model": route.get("model"), "reasoning_effort": route.get("reasoning_effort"), "profile": None}
    route.update(model=climb["model"], reasoning_effort=climb["reasoning_effort"])
    event = {
        "at": dt.datetime.now(dt.UTC).isoformat(),
        "role": ROLE,
        "trigger": TRIGGER,
        "detail": str(detail),
        "previous": previous,
        "selected": dict(climb),
    }
    if struggle_id is not None:
        event["struggle_id"] = struggle_id
    state.setdefault("reasoning_escalations", []).append(event)
    old_session = state.setdefault("sessions", {}).pop(ROLE, None)
    if old_session:
        state.setdefault("session_rotations", []).append(
            {
                "role": ROLE,
                "old_session": old_session,
                "at": event["at"],
                "reason": f"Plan sent back twice: Planner moved to {climb['profile']}",
            }
        )
    return event
