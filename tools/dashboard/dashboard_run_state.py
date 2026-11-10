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
# The dashboard still names routes and stages by their old code names (glm, astra_discovery, terra), while
# a new run saves the job names (planner, draft_plan, builder; autocode_names). read() hands it the old
# names until the dashboard moves to the job names.
LEGACY = {
    **{new: old for old, new in names.ROUTES.items() if old in names.RETIRED_ROUTES},
    **{new: old for old, new in names.STAGES.items()},
}
# Where a saved state names a route or a stage: string values under these keys anywhere in it, and the
# keys of these tables.
NAMED = frozenset({"stage", "next_stage", "role", "route_role", "origin", "original_stage", "source_stage"})
TABLES = (("settings", "roles"), ("sessions",), ("planning", "reports"))


def path(run) -> Path:
    return Path(run) / NAME


def read(run) -> dict:
    """The run's saved state; OSError or ValueError when it is unreadable or not an object."""
    value = json.loads(path(run).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("State is not an object")
    return legacy_names(value)


def _old(name):
    """The old code name of a route or stage (``draft_plan_report_repair`` -> ``astra_discovery_report_repair``)."""
    if not isinstance(name, str):
        return name
    base = name.removesuffix("_report_repair")
    return LEGACY[base] + name[len(base) :] if base in LEGACY else name


def _named(node) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in NAMED and isinstance(value, str):
                node[key] = _old(value)
            else:
                _named(value)
    elif isinstance(node, list):
        for item in node:
            _named(item)


def legacy_names(saved: dict) -> dict:
    """``saved`` (a run state) with its renamed routes and stages given back their old names."""
    for keys in TABLES:
        table: object = saved
        for key in keys[:-1]:
            table = table.get(key) if isinstance(table, dict) else None
        if isinstance(table, dict) and isinstance(table.get(keys[-1]), dict):
            table[keys[-1]] = {_old(name): value for name, value in table[keys[-1]].items()}
    _named(saved)
    return saved
