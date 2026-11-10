"""The dashboard's only raw read of a run's saved state (state.json).

The dashboard is a client of the core: detail panes read the public status
view (``status_view``, from ``autocode --status``) instead. The run list still
shows saved fields the view does not carry, so those reads stay here, in one
place, until they move to the view (#730).
"""

import json
from pathlib import Path

try:
    from .. import autocode_names as names
except ImportError:
    import autocode_names as names

NAME = "state.json"
# The dashboard still names routes by their old code names (glm, astra), while a new run saves the
# renamed ones (planner, task_planner; autocode_names). read() hands it the old names until the
# dashboard moves to the job names.
LEGACY_ROUTES = {new: old for old, new in names.ROUTES.items() if old in names.RETIRED_ROUTES}


def path(run) -> Path:
    return Path(run) / NAME


def read(run) -> dict:
    """The run's saved state; OSError or ValueError when it is unreadable or not an object."""
    value = json.loads(path(run).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("State is not an object")
    return legacy_routes(value)


def legacy_routes(state: dict) -> dict:
    """``state`` with its renamed route names given back their old names, where the dashboard reads them."""
    settings = state.get("settings")
    if isinstance(settings, dict) and isinstance(settings.get("roles"), dict):
        settings["roles"] = {LEGACY_ROUTES.get(role, role): route for role, route in settings["roles"].items()}
    records = [*(state.get("stages") or ()), state.get("active_stage")]
    for record in records:
        if isinstance(record, dict):
            for key in ("role", "route_role"):
                if record.get(key) in LEGACY_ROUTES:
                    record[key] = LEGACY_ROUTES[record[key]]
    return state
