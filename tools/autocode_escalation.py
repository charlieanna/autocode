"""Durable, evidence-triggered model/reasoning escalation for workflow roles.

Which rung a role climbs to, and every reason it stays, is
autocode_route_ladder.next_route's decision (shared with the Builder's
strong-retry policy); this module owns the state change: the one-rung
advance, the session rotation and the escalation event. ``unclimbed`` reads
a role's route back as the rung its recorded climbs left, for a binding that
a climb must not invalidate.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_route_ladder as route_ladder
except ImportError:
    import autocode_route_ladder as route_ladder

# Historical name for the static effort tables; a new run persists its
# serveability-filtered ladders under settings["route_ladders"] instead.
LADDERS = route_ladder.EFFORT_LADDERS


def rung(role, config):
    """Return the exact configured rung, or None for an explicit custom route."""
    return route_ladder.rung_index(LADDERS.get(role, ()), config.get("model"), config.get("reasoning_effort"))


def profile(role, config):
    index = rung(role, config)
    return LADDERS[role][index][2] if index is not None else None


def unclimbed(state: RunState, settings: dict, role: str) -> dict:
    """``settings`` with ``role``'s recorded climbs undone, newest first.

    A climb answers the role's own rejected or abandoned attempt. It is not new
    evidence or a new cause, so a binding taken before it (a recovery packet)
    still holds after it. Only a route still on the climbed rung is undone; any
    other model or effort change stays visible to the binding.
    """
    settings = copy.deepcopy(settings)
    route = (settings.get("roles") or {}).get(role)
    if not isinstance(route, dict):
        return settings
    for event in reversed(state.get("reasoning_escalations") or []):
        if not isinstance(event, dict) or event.get("role") != role:
            continue
        previous, selected = event.get("previous") or {}, event.get("selected") or {}
        if previous.get("model") and (route.get("model"), route.get("reasoning_effort")) == (
            selected.get("model"),
            selected.get("reasoning_effort"),
        ):
            route.update(model=previous.get("model"), reasoning_effort=previous.get("reasoning_effort"))
    return settings


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
