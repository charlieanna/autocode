"""Read-only launch identity shared by recovery offers and relaunch admission."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_retained_work as retained_work
    from . import autocode_util as util
except ImportError:
    import autocode_resolver_recovery as resolver_recovery
    import autocode_retained_work as retained_work
    import autocode_util as util

BINDING_KEYS = ("contract_hash", "contract_revision", "task_id", "source_revision", "settings_hash", "scope")
UNBOUND = "launch_binding"
# Budgets the run itself extends between a failed attempt and its relaunch: after a stage timeout,
# AutoResolver may raise the default stage timeout (autocode_default_budget_runtime.recover). Like the
# ceilings the established binding already leaves out, a budget is not the source, task, settings,
# contract or scope a relaunch acts on.
RUN_EXTENDED_LIMITS = ("stage_timeout_seconds",)


def _snapshot(ref):
    try:
        value = util.read(Path(ref)) if isinstance(ref, (str, Path)) else None
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("files"), dict) else None


def _comparable(state: RunState):
    """``state`` with the budgets the run extends itself left out of the settings it binds."""
    settings = state.get("settings")
    limits = settings.get("limits") if isinstance(settings, dict) else None
    if (
        not isinstance(settings, dict)
        or not isinstance(limits, dict)
        or not any(key in limits for key in RUN_EXTENDED_LIMITS)
    ):
        return state
    kept = {key: value for key, value in limits.items() if key not in RUN_EXTENDED_LIMITS}
    return {**state, "settings": {**copy.deepcopy(settings), "limits": kept}}


def capture_launch_binding(state: RunState, record, before) -> None:
    """Record the source, task, settings, contract and scope this launch actually used.

    Written at the shared launch admission, before the provider process starts: the
    attempt's recovery later copies this value instead of recomputing one from
    then-current values after drift.
    """
    record["launch_binding"] = {
        **resolver_recovery._binding(_comparable(state), before["revision"]),
        "scope": resolver_recovery._scope(state),
    }


def preserved_launch_binding(record):
    """The failed attempt's launch binding for its recovery row: copied, never recomputed.

    Where the attempt preserved no captured binding (a run saved before the capture
    existed), derive only what its own recorded evidence proves — its before-snapshot
    revision and the task and contract its record names.  When the captured or derived
    binding lacks a recorded ``settings_hash`` or ``scope`` authority the missing
    component is left as None so ``_drift`` treats it as unverified rather than
    silently skipping it; nothing recorded at all returns None and the relaunch is
    refused outright.  Falling back to current values is the defect this fixes.
    """
    captured = record.get("launch_binding") if isinstance(record.get("launch_binding"), dict) else {}
    derived = {
        "contract_hash": captured.get("contract_hash") or record.get("contract_hash"),
        "contract_revision": captured.get("contract_revision") or record.get("contract_revision"),
        "task_id": captured.get("task_id") or record.get("task_id"),
        "source_revision": captured.get("source_revision"),
        "settings_hash": captured.get("settings_hash"),
        "scope": captured.get("scope"),
    }
    start = _snapshot(record.get("before_ref"))
    if isinstance(start, dict) and derived["source_revision"] is None:
        derived["source_revision"] = start.get("revision")
    if not any(derived.get(key) is not None for key in BINDING_KEYS):
        return None
    return derived


def _settings_hold(bound, state: RunState, revision) -> bool:
    """Whether the bound settings still hold, with the transitions ``_holds`` already admits."""
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    projected = {
        "contract_hash": contract.get("hash"),
        "contract_revision": contract.get("revision"),
        "task_id": task.get("id"),
        "source_revision": revision,
        "settings_hash": bound.get("settings_hash"),
    }
    return resolver_recovery._holds(projected, _comparable(state), revision)


def _drift(bound, state: RunState, current):
    """The components that moved on outside the run since the failed attempt's launch."""
    moved = []
    revision = current.get("revision")
    current_binding = resolver_recovery._binding(state, revision)
    if bound.get("source_revision") is not None and bound["source_revision"] != revision:
        own = retained_work.own_recovery_source(
            state.get("stages", []),
            bound["source_revision"],
            {"head": current.get("head"), "files": current.get("files") or {}},
        )
        if not own:
            moved.append("source")
    if bound.get("task_id") is not None and bound["task_id"] != current_binding.get("task_id"):
        moved.append("task")
    if (bound.get("contract_hash") is not None and bound["contract_hash"] != current_binding.get("contract_hash")) or (
        bound.get("contract_revision") is not None
        and bound["contract_revision"] != current_binding.get("contract_revision")
    ):
        moved.append("contract")
    if bound.get("settings_hash") is None or not _settings_hold(bound, state, revision):
        moved.append("settings")
    if not isinstance(bound.get("scope"), dict) or bound["scope"] != resolver_recovery._scope(state):
        moved.append("scope")
    return moved
