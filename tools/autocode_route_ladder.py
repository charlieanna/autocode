"""One route ladder per role: the rungs a struggling stage may climb.

Pure domain logic over route tuples; imports nothing from AutoCode. Both
escalation paths consume it, so a run has one answer to "what can this role
climb to, and does its provider serve it":

- autocode_escalation climbs a role's effort rungs on evidence of struggle;
- autocode_builder_policy sends the Builder to the strong rung after its
  ordinary retries are spent.

Rungs are (model, reasoning_effort, label) tuples with provider-qualified
models; persisted rungs in settings["route_ladders"] are lists (state.json
is JSON), which index the same way.
"""
from __future__ import annotations

# Effort rungs per role, strongest last. Only the Resolver (astra) climbs
# GPT-6 Astra: it is too expensive for any other role (user 2026-09-28). A
# role whose route matches no rung (the default GLM Builder, a GLM Validator
# or Completion Owner, the Plan Reviewer) has no effort ladder; the Builder's
# stronger attempt is the strong rung from its retry policy instead.
EFFORT_LADDERS = {
    "astra": (
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
    return next((index for index, (candidate, reasoning, _label) in enumerate(ladder)
                 if candidate == model and reasoning == effort), None)


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
    return {role: tuple(rung for rung in ladder if rung[0] in listed_models)
            for role, ladder in ladders.items()}


def configure_ladders(listed_models=None):
    """A new run's persisted ladders: the effort tables, serveability-filtered
    for a provider that publishes its models. Rungs are lists, the shape
    state.json keeps, so a saved run re-reads what it persisted. The Builder's
    strong rung is not part of this table; its retry policy already persists it."""
    served = served_ladders(EFFORT_LADDERS, listed_models)
    return {"effort": {role: [list(rung) for rung in ladder] for role, ladder in served.items()}}


def ladders(settings):
    """A run's effort ladders as tuples of rung tuples: the persisted
    serveability-filtered table, or the static tables for runs saved before
    route_ladders existed."""
    persisted = (settings or {}).get("route_ladders") or {}
    effort = persisted.get("effort")
    if not effort:
        return EFFORT_LADDERS
    return {role: tuple(tuple(rung) for rung in ladder) for role, ladder in effort.items()
            if ladder}


def record_outcome(state, outcome):
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


def outcome_summary(state):
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
