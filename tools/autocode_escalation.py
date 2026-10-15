"""Durable, evidence-triggered model/reasoning escalation for workflow roles.

Which rung a role climbs to, and every reason it stays, is
autocode_route_ladder.next_route's decision (shared with the Builder's
strong-retry policy); this module owns the state change: the one-rung
advance, the session rotation and the escalation event.
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
    run saved one, else the static table, keyed by route role (the Resolver's is
    "resolver"). The same lookup next_route makes for advance()."""
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
    without silently inventing another provider route. Returns the escalation
    event, or None when next_route keeps the role where it is.
    """
    settings = state.get("settings", {})
    climb = route_ladder.next_route(
        settings, role, {"struggle_id": struggle_id, "escalations": state.get("reasoning_escalations", [])}
    )
    if "stay" in climb:
        return None
    config = settings["roles"][role]
    previous = {
        "model": config.get("model"),
        "reasoning_effort": config.get("reasoning_effort"),
        "profile": climb["previous_profile"],
    }
    effort, label = climb["reasoning_effort"], climb["profile"]
    config.update(model=climb["model"], reasoning_effort=effort)
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
