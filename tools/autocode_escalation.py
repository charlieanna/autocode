"""Durable, evidence-triggered model/reasoning escalation for workflow roles.

The route tables and rung matching live in autocode_route_ladder (shared with
the Builder's strong-retry policy); this module owns the state change: the
guards, the one-rung advance, the session rotation and the escalation event.
"""

from __future__ import annotations

import datetime as dt

try:
    from . import autocode_route_ladder as route_ladder
except ImportError:
    import autocode_route_ladder as route_ladder

# Historical name for the static effort tables; a new run persists its
# serveability-filtered ladders under settings["route_ladders"] instead.
LADDERS = route_ladder.EFFORT_LADDERS


def ladder(role, settings=None):
    """The rungs a route role may climb in a run: its persisted ladder when the
    run saved one, else the static table. The one lookup rung(), profile() and
    advance() share, keyed by route role (the Resolver's is "resolver")."""
    return route_ladder.ladders(settings).get(role, ())


def rung(role, config, settings=None):
    """Return the exact configured rung, or None for an explicit custom route."""
    return route_ladder.rung_index(ladder(role, settings), config.get("model"), config.get("reasoning_effort"))


def profile(role, config, settings=None):
    index = rung(role, config, settings)
    return ladder(role, settings)[index][2] if index is not None else None


def advance(state, role, *, trigger, detail="", struggle_id=None):
    """Advance one rung for the next request and rotate the prior role session.

    Custom providers/models are deliberately left alone. Reaching the final rung is
    also stable: struggle remains recorded by the rejected report or validation,
    without silently inventing another provider route.
    """
    settings = state.get("settings", {})
    # This opt-in conversation profile fixes every role's model and effort.
    # Recovery still uses its configured Builder retry policy, including its
    # same-Sol-High attempt; the generic ladder must not promote those routes.
    if settings.get("conversation_profile") == "continuous-v1":
        return None
    roles = settings.get("roles", {})
    if struggle_id is not None and any(
        event.get("role") == role and event.get("struggle_id") == struggle_id
        for event in state.get("reasoning_escalations", [])
    ):
        return None
    config = roles.get(role)
    if not isinstance(config, dict) or config.get("model_pinned") or config.get("provider") not in (None, "openai"):
        return None
    rungs = ladder(role, settings)
    index = route_ladder.rung_index(rungs, config.get("model"), config.get("reasoning_effort"))
    if index is None:
        return None
    selected = route_ladder.next_rung(rungs, config.get("model"), config.get("reasoning_effort"))
    if selected is None:
        return None
    model, effort, label = selected
    previous = {
        "model": config.get("model"),
        "reasoning_effort": config.get("reasoning_effort"),
        "profile": rungs[index][2],
    }
    engine = config.get("engine", settings.get("engine", "codex"))
    config.update(model=route_ladder.format_model(model, engine), reasoning_effort=effort)
    old_session = state.setdefault("sessions", {}).pop(role, None)
    event = {
        "at": dt.datetime.now(dt.UTC).isoformat(),
        "role": role,
        "trigger": trigger,
        "detail": str(detail),
        "previous": previous,
        "selected": {"model": config["model"], "reasoning_effort": effort, "profile": label},
    }
    if struggle_id is not None:
        event["struggle_id"] = struggle_id
    state.setdefault("reasoning_escalations", []).append(event)
    if old_session:
        state.setdefault("session_rotations", []).append(
            {
                "role": role,
                "old_session": old_session,
                "at": event["at"],
                "reason": f"Automatic escalation: {previous['profile']} → {label}",
            }
        )
    return event
