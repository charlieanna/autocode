"""One route ladder per role: the rungs a struggling stage may climb.

Pure domain logic over route tuples; imports only autocode_verifier_independence
(and RunState, for type checking only).
Both escalation paths consume it, so a run has one answer to "what can this role
climb to, and does its provider serve it":

- autocode_escalation climbs a role's effort rungs on evidence of struggle, on
  the rung next_route names;
- autocode_builder_policy sends the Builder to the strong rung after its
  ordinary retries are spent.

A new run's rungs are checked against the models its provider offers before any
model call (``serve``), so a climb never lands on a model the provider cannot
serve.

Rungs are (model, reasoning_effort, label) tuples with provider-qualified
models; persisted rungs in settings["route_ladders"] are lists (state.json
is JSON), which index the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_verifier_independence as independence

# Effort rungs per route role (the settings["roles"] key a stage runs on and
# escalates by), strongest last. Only the Resolver climbs GPT-6 Astra: it is
# too expensive for any other role (user 2026-09-28). The Resolver stages run
# on their own "resolver" route, which starts as a copy of the "astra" route;
# the per-task Planner (astra_plan) stays on "astra" and climbs nothing (#1345).
# A role whose route matches no rung (the default GLM Builder, a GLM Validator
# or Completion Owner, the Plan Reviewer) has no effort ladder; the Builder's
# stronger attempt is the strong rung from its retry policy instead.
EFFORT_LADDERS = {
    "resolver": (
        ("openai/gpt-6-astra", "high", "GPT-6 Astra High"),
        ("openai/gpt-6-astra", "xhigh", "GPT-6 Astra XHigh"),
        ("openai/gpt-6-astra", "max", "GPT-6 Astra Max"),
    ),
    "terra": (
        ("openai/gpt-6-sol", "medium", "GPT-6 Sol Medium"),
        ("openai/gpt-6-sol", "high", "GPT-6 Sol High"),
        ("openai/gpt-6-sol", "xhigh", "GPT-6 Sol XHigh"),
        ("openai/gpt-6-sol", "max", "GPT-6 Sol Max"),
    ),
    "sol": (
        ("openai/gpt-6-sol", "high", "GPT-6 Sol High"),
        ("openai/gpt-6-sol", "xhigh", "GPT-6 Sol XHigh"),
        ("openai/gpt-6-sol", "max", "GPT-6 Sol Max"),
    ),
    "completion": (
        ("openai/gpt-6-sol", "medium", "GPT-6 Sol Medium"),
        ("openai/gpt-6-sol", "high", "GPT-6 Sol High"),
        ("openai/gpt-6-sol", "max", "GPT-6 Sol Max"),
    ),
}


def normalize_model(model):
    """Provider-qualify a bare OpenAI model; other prefixes are kept."""
    if not isinstance(model, str):
        return model
    return model if "/" in model else f"openai/{model}"


def format_model(model, engine):
    """A rung model as the role's engine names it: OpenCode keeps the
    provider qualification, the Codex engine keeps bare names. A model from
    another provider is never stripped to bare."""
    if engine == "opencode" or not isinstance(model, str) or "/" not in model:
        return model
    return model.split("/", 1)[1] if model.startswith("openai/") else model


def rung_index(ladder, model, effort):
    """The 0-based rung a route sits on, or None for a custom route."""
    model = normalize_model(model)
    return next(
        (
            index
            for index, (candidate, reasoning, _label) in enumerate(ladder)
            if candidate == model and reasoning == effort
        ),
        None,
    )


def next_rung(ladder, model, effort):
    """The rung after the route's rung, or None when absent or final."""
    index = rung_index(ladder, model, effort)
    if index is None or index + 1 >= len(ladder):
        return None
    return ladder[index + 1]


def strong_rung(builder_retry):
    """The Builder's strong (model, effort) from its retry policy, or None
    when the run's provider offers no stronger Builder model."""
    config = builder_retry or {}
    if not config.get("strong_model"):
        return None
    return normalize_model(config["strong_model"]), config.get("strong_reasoning_effort")


def unserved(model, listed_models):
    """Whether a provider that publishes its model list cannot serve a route.
    None means the list is unknown (every rung kept); an empty list serves
    nothing."""
    return listed_models is not None and model not in listed_models


def served_ladders(ladders, listed_models):
    """Ladders with rungs the provider cannot serve removed. A provider that
    does not publish a list keeps every rung (its catalogue is unknown)."""
    if listed_models is None:
        return ladders
    return {role: tuple(rung for rung in ladder if rung[0] in listed_models) for role, ladder in ladders.items()}


def configure_ladders(listed_models=None):
    """A new run's persisted ladders: the effort tables, serveability-filtered
    for a provider that publishes its models. Rungs are lists, the shape
    state.json keeps, so a saved run re-reads what it persisted. The Builder's
    strong rung is not part of this table; its retry policy already persists it."""
    served = served_ladders(EFFORT_LADDERS, listed_models)
    return {"effort": {role: [list(rung) for rung in ladder] for role, ladder in served.items()}}


# Ladder keys a saved run may persist under a route role that no longer
# climbs them, and the route role they belong to. Runs saved before #1345 keyed
# the Resolver's Astra ladder "astra", the per-task Planner's route.
SAVED_LADDER_ROLES = {"astra": "resolver"}


def ladders(settings):
    """A run's effort ladders as tuples of rung tuples: the persisted
    serveability-filtered table, or the static tables for runs saved before
    route_ladders existed. A ladder saved under an old key is read as its
    route role's (SAVED_LADDER_ROLES), without rewriting the saved state."""
    persisted = (settings or {}).get("route_ladders") or {}
    effort = persisted.get("effort")
    if not effort:
        return EFFORT_LADDERS
    effort = dict(effort)
    for saved, role in SAVED_LADDER_ROLES.items():
        if saved in effort:
            effort.setdefault(role, effort.pop(saved))
    return {role: tuple(tuple(rung) for rung in ladder) for role, ladder in effort.items() if ladder}


# Why next_route keeps a role on its route instead of naming a rung.
STAYS = {
    "fixed_profile": "the continuous-v1 conversation profile fixes every role's model and effort",
    "climbed": "the role already climbed one rung for this struggle",
    "no_route": "the run has no route for the role",
    "pinned": "a person pinned the role's model",
    "other_provider": "the role runs on a custom provider, which AutoCode never changes",
    "off_ladder": "the role's model and effort are on no rung of its ladder",
    "ladder_end": "the role is on its ladder's last rung",
}


def next_route(settings, role, evidence=None):
    """The rung a struggling role climbs to next, or why it stays on its route (#612).

    The one decision for a role's effort climb; autocode_escalation applies it. ``evidence``
    describes the struggle: ``struggle_id`` names the failed iteration and ``escalations`` lists
    the run's recorded climbs, so one struggle climbs at most one rung. The rungs are the run's
    persisted ladders, which hold only rungs its provider offers (``serve``).

    Returns ``{"model", "reasoning_effort", "profile", "previous_profile"}``, the model spelled as
    the role's engine names it, or ``{"stay": reason}`` with a reason from STAYS.
    """
    settings = settings or {}
    evidence = evidence or {}
    # This opt-in conversation profile fixes every role's model and effort. Recovery still uses its
    # configured Builder retry policy, including its same-Sol-High attempt; the ladder must not
    # promote those routes.
    if settings.get("conversation_profile") == "continuous-v1":
        return {"stay": "fixed_profile"}
    struggle = evidence.get("struggle_id")
    if struggle is not None and any(
        event.get("role") == role and event.get("struggle_id") == struggle
        for event in evidence.get("escalations") or ()
    ):
        return {"stay": "climbed"}
    route = (settings.get("roles") or {}).get(role)
    if not isinstance(route, dict):
        return {"stay": "no_route"}
    if route.get("model_pinned"):
        return {"stay": "pinned"}
    if route.get("provider") not in (None, "openai"):
        return {"stay": "other_provider"}
    ladder = ladders(settings).get(role, ())
    index = rung_index(ladder, route.get("model"), route.get("reasoning_effort"))
    if index is None:
        return {"stay": "off_ladder"}
    rung = next_rung(ladder, route.get("model"), route.get("reasoning_effort"))
    if rung is None:
        return {"stay": "ladder_end"}
    model, effort, label = rung
    return {
        "model": format_model(model, route.get("engine", settings.get("engine", "codex"))),
        "reasoning_effort": effort,
        "profile": label,
        "previous_profile": ladder[index][2],
    }


def offered(model, available):
    """Whether a provider's model list holds a rung's model, as spelled or provider-qualified."""
    return model in available or normalize_model(model) in available


def serve(settings, available, *, named_strong=None):
    """Check every rung a new run may climb to against the models its provider offers (#612).

    ``available`` is the provider's model list, read once for the run's routes; None (a provider
    that cannot list) keeps every rung. Only routes on the OpenCode engine are checked, the engine
    that list describes. An effort rung the provider does not offer is removed. The Builder's
    stronger attempt moves two routes: the Builder onto the strong model and, when a Tester or
    Completion Reviewer runs that model or its family, the checker onto ``checker_model``. When
    the provider offers either model not, the run has no stronger attempt (the Builder gets one
    more ordinary retry in its place, then pauses naming the model, recorded as ``not_offered``),
    or, when the person named the strong model (``named_strong``), the run is refused here with a
    ValueError, before any model call, rather than failing the launch mid-run.

    Updates ``settings`` in place and returns it.
    """
    if available is None:
        return settings
    available = set(available)
    effort = (settings.get("route_ladders") or {}).get("effort")
    if effort:
        settings["route_ladders"]["effort"] = {
            role: [
                list(rung) for rung in ladder if _engine(settings, role) != "opencode" or offered(rung[0], available)
            ]
            for role, ladder in effort.items()
        }
    retry = settings.get("builder_retry")
    missing = _unoffered_climb(settings, retry, available) if isinstance(retry, dict) else None
    if missing is None:
        return settings
    if named_strong:
        raise ValueError(
            f"--builder-strong-model {named_strong} is not offered by this run's provider; "
            "name a model it lists (`autocode models`), or leave the flag out"
            if missing == retry["strong_model"]
            else f"--builder-strong-model {named_strong}: its stronger attempt moves a Tester or Completion "
            f"Reviewer on that model's family to {missing}, which this run's provider does not offer; "
            "name a model outside their family, or leave the flag out"
        )
    retry.update(strong_model=None, not_offered=missing)
    return settings


def _engine(settings, role):
    route = (settings.get("roles") or {}).get(role)
    return (route if isinstance(route, dict) else {}).get("engine") or settings.get("engine") or "codex"


def movable(settings, route):
    """Whether the Builder's stronger attempt may move this checker route to the checker model.

    Never a pinned route or one on a custom provider, and never a bare model name (a native Codex
    one) unless a configured command tool serves the route, since such a tool spells its models
    itself (settings["provider"] names its config). autocode_builder_policy.swap_checkers asks this.
    """
    tool = (
        settings.get("provider") not in (None, "opencode") and route.get("engine", settings.get("engine")) == "opencode"
    )
    return not (
        route.get("model_pinned")
        or route.get("provider") not in (None, "openai")
        or ("/" not in str(route.get("model")) and not tool)
    )


def _unoffered_climb(settings, retry, available):
    """The model of the stronger attempt the provider does not offer, or None.

    None too when the run could never make that attempt: a pinned Builder, or one on a custom
    provider, pauses instead (autocode_builder_policy._escalate), so its rungs launch nothing.
    """
    strong = retry.get("strong_model")
    builder = (settings.get("roles") or {}).get("terra")
    if (
        not strong
        or not isinstance(builder, dict)
        or builder.get("model_pinned")
        or builder.get("provider") not in (None, "openai")
        or _engine(settings, "terra") != "opencode"
    ):
        return None
    if not offered(strong, available):
        return strong
    checker = retry.get("checker_model")
    if not checker or independence.may_share(strong, checker) or offered(checker, available):
        # A checker on the strong model's family moves only to a different model (swap_checkers).
        return None
    roles = settings.get("roles") or {}
    moved = [
        role
        for role in independence.verifiers("terra")
        if isinstance(roles.get(role), dict)
        and _engine(settings, role) == "opencode"
        and movable(settings, roles[role])
        and independence.may_share(strong, roles[role].get("model"))
    ]
    return checker if moved else None


def record_outcome(state: RunState, outcome):
    """Stamp every escalation decision that has no outcome yet.

    Written where the struggle's result is known: a validation PASS passed
    the retried work, a retry-limit pause ended it. Events stay open
    otherwise — an open event is an honest unknown, not a success.
    """
    stamped = []
    for event in state.get("reasoning_escalations", []):
        if "outcome" not in event:
            event["outcome"] = outcome
            stamped.append(event)
    for decision in state.get("builder_retry_decisions", []):
        if decision.get("action") in ("retry", "escalate") and "outcome" not in decision:
            decision["outcome"] = outcome
            stamped.append(decision)
    return stamped


def outcome_summary(state: RunState):
    """How the run's escalations resolved, for the status view."""
    counts = {"passed": 0, "paused": 0, "open": 0}
    for event in state.get("reasoning_escalations", []):
        key = event.get("outcome", "open")
        counts[key] = counts.get(key, 0) + 1
    for decision in state.get("builder_retry_decisions", []):
        if decision.get("action") in ("retry", "escalate"):
            key = decision.get("outcome", "open")
            counts[key] = counts.get(key, 0) + 1
    return counts
