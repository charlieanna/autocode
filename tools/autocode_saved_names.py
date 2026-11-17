"""Read a run saved under AutoCode's old code names with the job names this runtime runs.

The code-name rename (owner decision 2026-11-10, #3090, #3097, #3110) renamed the routes
(``glm``, ``astra``, ``terra``, ``sol``), the stages (``astra_discovery`` ...) and the planning
record's review counter (``astra_calls``). A run saved before it was refused at resume with
"This chat can't continue", although nothing about its work had changed: only the words.

``migrate`` gives such a saved run the new names, so it continues where it stopped. It is a
deterministic rename read from the one table (autocode_names), and it changes nothing a seal or
a digest covers:

- A sealed goal contract's ``body`` keeps its words: its hash binds the plan the person approved,
  and the plan's terms never named a route.
- Records filed under a digest of their own content (the Resolver's escalations and receipts,
  failure identities) and stage receipts keep their words: each is evidence of what happened, under
  the names that were current then, and is read by the identity it was filed with.

Everything else the runtime reads by name (the configured routes, sessions, the next stage, the
planning record, the stage history and the questions and events naming a stage) is renamed. A
receipt or a Resolver record that binds an old stage name is compared through ``same_stage``, so
the question a person was asked before the rename is still the open question after it.

Pure: imports only autocode_names, so any layer may call it.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_names as names
except ImportError:
    import autocode_names as names

# Old name (route or stage) -> its job name. ``terra`` and ``sol`` are both a route and a stage,
# with the same job name either way.
RENAMED = {
    **{old: names.ROUTES[old] for old in names.RETIRED_ROUTES},
    **names.STAGES,
}
# The plan origin the Planner's draft had before the rename (#3110).
OLD_ORIGINS = {"glm_draft": "planner_draft"}
REPAIR = "_report_repair"
# String fields that hold a route or a stage name, wherever they are in the saved state.
NAMED_FIELDS = frozenset(
    {
        "stage",
        "next_stage",
        "role",
        "route_role",
        "original_stage",
        "source_stage",
        "return_stage",
        "reviewer_role",
        "then",
        "source",
        "origin",
    }
)
# Tables keyed by route or stage name, by the field that holds them.
NAMED_TABLES = frozenset(
    {
        "roles",
        "sessions",
        "reports",
        "effort_origins",
        "route_origins",
        "effort_fits",
        "effort",
        "seconds_by_role",
    }
)
# Kept as evidence under the names current when they were written; see the module docstring.
KEPT = frozenset({"resolver", "receipt", "failure_history", "evaluation_binding", "binding", "identity"})


def rename(name):
    """The job name of an old route, stage or report repair; any other value as it is."""
    if not isinstance(name, str):
        return name
    if name in OLD_ORIGINS:
        return OLD_ORIGINS[name]
    base = name.removesuffix(REPAIR)
    return RENAMED[base] + name[len(base) :] if base in RENAMED else name


def same_stage(saved, current) -> bool:
    """Whether a name a record saved and a current name are the same stage or route, spelled old or new."""
    return saved == current or rename(saved) == rename(current)


def old_names(state: RunState) -> list[str]:
    """The routes the saved run's settings name by an old name, sorted; empty for a current run."""
    roles = ((state.get("settings") or {}).get("roles")) if isinstance(state, dict) else None
    old = [role for role in (roles or {}) if role in names.RETIRED_ROUTES]
    planning = state.get("planning") if isinstance(state, dict) else None
    if isinstance(planning, dict) and names.OLD_PLANNING_COUNTER in planning:
        old.append(names.OLD_PLANNING_COUNTER)
    return sorted(old)


def needed(state: RunState) -> bool:
    """Whether ``state`` was saved under the old names and ``migrate`` would rename it."""
    return bool(old_names(state))


def _sealed(node: dict) -> bool:
    return {"task_id", "revision", "body", "hash"} <= node.keys()


def _table(table: dict, where: str) -> dict:
    out: dict = {}
    for key, value in table.items():
        new = rename(key)
        if new in out and new != key:
            raise ValueError(f"{where} names {new} twice, as {new} and as its old name {key}")
        if new in out:
            raise ValueError(f"{where} names {new} twice")
        out[new] = value
    return out


def _walk(node, where: str):
    if isinstance(node, list):
        return [_walk(item, where) for item in node]
    if not isinstance(node, dict):
        return node
    sealed = _sealed(node)
    out: dict = {}
    for key, value in node.items():
        path = f"{where}.{key}"
        if key in KEPT or (sealed and key == "body"):
            out[key] = value
            continue
        if key == names.OLD_PLANNING_COUNTER:
            if "plan_review_calls" in node:
                raise ValueError(f"{where} counts Plan Reviewer calls twice")
            key = "plan_review_calls"
        if key in NAMED_FIELDS and isinstance(value, str):
            out[key] = rename(value)
        elif key in NAMED_TABLES and isinstance(value, dict):
            out[key] = _walk(_table(value, path), path)
        else:
            out[key] = _walk(value, path)
    return out


def renamed(node):
    """A copy of a saved record (a Resolver request's origin, say) with its route and stage names renamed."""
    return _walk(copy.deepcopy(node), "record")


def migrate(state: RunState) -> bool:
    """Give a run saved under the old names the job names, in place; whether anything was renamed.

    Raises ValueError when the saved state names one route or counter both ways, which no
    rename can settle; the caller then keeps refusing the run as before.
    """
    if not needed(state):
        return False
    renamed = _walk(state, "state")
    state.clear()
    state.update(renamed)
    return True
