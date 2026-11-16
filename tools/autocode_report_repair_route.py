"""Restore a released temporary Investigator route for its saved report repair.

The original attempt owns the model/engine/provider/effort. This restores only
settings.roles.stuck_investigator, never stage choices or a new transport binding.
The ordinary transport, source and permission gates still run before launch.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


TEMPORARY_ROUTES = {
    "investigate_stuck": "stuck_investigator",
    "resolve_repair": "repair_resolver",
}


def release_settings(settings: dict | None, route: str) -> dict:
    """Remove a temporary derived route and any inherited transport pin from ``settings``."""
    if not isinstance(settings, dict):
        return {}
    roles = settings.get("roles")
    popped = roles.pop(route, None) if isinstance(roles, dict) else None
    role_key = f"role:{route}"
    for key in ("transport_identities", "provider_transports"):
        mapping = settings.get(key)
        if isinstance(mapping, dict) and role_key in mapping:
            mapping.pop(role_key, None)
            if not mapping:
                settings.pop(key, None)
    return popped if isinstance(popped, dict) else {}


def release(state: RunState, route: str) -> dict:
    """Remove a temporary derived route, its transport pin, and any saved session from ``state``."""
    used = release_settings(state.get("settings"), route)
    sessions = state.get("sessions")
    if isinstance(sessions, dict):
        sessions.pop(route, None)
    return used


def restore(state: RunState, original):
    expected_route = TEMPORARY_ROUTES.get(str(original.get("stage") or ""))
    if not expected_route or original.get("route_role") != expected_route:
        return
    route = original.get("launch_route")
    if not isinstance(route, dict) or not isinstance(route.get("model"), str) or not route["model"]:
        raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Saved temporary-route repair has no original launch route")
    route = copy.deepcopy(route)
    # launch_route records absent role fields as None; the attempt's engine is
    # the resolved value actually used, including the run-level default.
    if route.get("engine") is None:
        route["engine"] = original.get("engine")
        if not isinstance(route["engine"], str) or not route["engine"]:
            raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Saved temporary-route repair has no original engine")
    roles = state["settings"]["roles"]
    existing = roles.get(expected_route)
    effective = dict(existing or {})
    if "engine" not in effective:
        effective["engine"] = state["settings"].get("engine", "codex")
    if existing is not None and any(effective.get(key) != value for key, value in route.items()):
        raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Temporary role route changed since its rejected report")
    if existing is None:
        roles[expected_route] = copy.deepcopy(route)
