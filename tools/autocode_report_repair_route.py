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


def restore(state: RunState, original):
    if original.get("stage") != "investigate_stuck" or original.get("route_role") != "stuck_investigator":
        return
    route = original.get("launch_route")
    if not isinstance(route, dict) or not isinstance(route.get("model"), str) or not route["model"]:
        raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Saved Investigator repair has no original launch route")
    route = copy.deepcopy(route)
    # launch_route records absent role fields as None; the attempt's engine is
    # the resolved value actually used, including the run-level default.
    if route.get("engine") is None:
        route["engine"] = original.get("engine")
        if not isinstance(route["engine"], str) or not route["engine"]:
            raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Saved Investigator repair has no original engine")
    roles = state["settings"]["roles"]
    existing = roles.get("stuck_investigator")
    effective = dict(existing or {})
    if "engine" not in effective:
        effective["engine"] = state["settings"].get("engine", "codex")
    if existing is not None and any(effective.get(key) != value for key, value in route.items()):
        raise util.Paused("PAUSED_STALE_REPORT_ROUTE", "Investigator route changed since its rejected report")
    if existing is None:
        roles["stuck_investigator"] = copy.deepcopy(route)
