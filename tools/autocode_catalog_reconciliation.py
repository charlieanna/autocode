"""Bound, metadata-only restart after the task's source inventory changes."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_catalog_context as context
    from . import autocode_repo_catalog as catalog
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
    from . import autocode_stop as stop
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_catalog_context as context
    import autocode_repo_catalog as catalog
    import autocode_roles as roles
    import autocode_run_status as run_status
    import autocode_stop as stop
    import autocode_util as util
    import autocode_workflows as workflows

PREFIX = "catalog-v1:"


def eligible(state: RunState):
    """Check the in-memory planning boundary without reading the catalog."""
    v2 = roles.planning_flow(state) == "v2"
    pause = state.get("pause_intent")
    if (
        state.get("status") != "PAUSED_SOURCE_UNAVAILABLE"
        or not str(state.get("stop_reason") or "").startswith("The task's source catalog changed:")
        or state.get("next_stage") not in (roles.PLANNING_V2_STAGES if v2 else roles.PLANNING_STAGES)
        or not (v2 or workflows.requirements_first(state))
        or (state.get("execution") or {}).get("mode") == "durable_local"
        or state.get("ui_run")
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
                "pending_questions",
                "user_request",
                "resolver_human_proposal",
                "resolver_human_request",
                "changeset_approval",
                "parent_run",
            )
        )
        or (pause and (not isinstance(pause, dict) or not pause.get("acknowledged_at")))
        or stop.applied_stop(state) is not None
    ):
        return None
    return True


def candidate(state: RunState):
    """Return current and original inventories only at a safe planning boundary."""
    if not eligible(state):
        return None
    old = context.saved_inventory(state)
    if not isinstance(old, dict):
        return None
    discovery = catalog.discover()
    if catalog.blocking(discovery):
        return None
    new = context.inventory(discovery, allow_empty=True)
    changes = context.binding_changes(old, discovery)
    if not isinstance(new, dict) or not changes:
        return None
    return old, new, changes


def token(state: RunState, old, new):
    return PREFIX + util.digest({"state": state, "old": old, "new": new})


def offer(state: RunState, *, controller=None, stop_pending=False):
    if stop_pending or not isinstance(controller, dict) or controller.get("kind") not in ("ended", "boundary"):
        return None
    try:
        found = candidate(state)
    except (OSError, ValueError, RuntimeError, TypeError, util.Paused):
        return None
    if not found:
        return None
    old, new, changes = found
    return {
        "kind": "continue",
        "action": "--reconcile-only " + token(state, old, new),
        "reason": "Source bindings changed ("
        + "; ".join(changes)
        + "). AutoCode will revisit Requirements and the plan.",
    }


def require_binding(state: RunState, expected):
    found = candidate(state)
    if not found or expected != token(state, found[0], found[1]):
        raise ValueError("Source inventory changed; refresh status before reconciling")
    return found


def reconcile(runner, state: RunState, run_dir, expected):
    """Write an immutable receipt, then atomically publish the invalidated planning state."""
    old, new, changes = require_binding(state, expected)
    selected = util.digest(new)
    if str(Path(run_dir).resolve()) != str(Path(state["run_dir"]).resolve()):
        raise ValueError("Source inventory receipt belongs to a different run")
    path = context.receipt_path({**state, "source_catalog_receipt": selected})
    assert path is not None  # require_binding admitted a saved run directory.
    if path.exists():
        if util.read_object(path) != new:
            raise ValueError("Source inventory receipt conflicts with its content identity")
    else:
        util.atomic_json(path, new)
    updated = copy.deepcopy(state)
    previous = updated.pop("goal_contract", None)
    if previous:
        updated.setdefault("contract_history", []).append(previous)
    handoff = updated.pop("requirements_handoff", None)
    if handoff:
        updated.setdefault("requirements_history", []).append(handoff)
    planning = updated.get("planning")
    if isinstance(planning, dict):
        updated.setdefault("planning_history", []).append(copy.deepcopy(planning))
        # Discard only obsolete artifacts, never the already-spent counters or limits.
        planning["reports"] = {}
        planning["final_token"] = None
        for key in ("inputs", "inherited_review"):
            planning.pop(key, None)
    runner.goals.invalidate(updated, "Source catalog changed; Requirements and plan must be reviewed again")
    updated.pop("requirements_artifact_token", None)
    prior_task = updated.pop("current_task", None)
    updated["source_catalog_receipt"] = selected
    updated.setdefault("user_events", []).append(
        {
            "kind": "source_catalog_reconciled",
            "actor": "runner",
            "at": util.now(),
            "old_receipt": util.digest(old),
            "new_receipt": selected,
            "changes": changes,
            "previous_task": prior_task,
        }
    )
    if roles.planning_flow(updated) == "v2":
        workflows.begin(updated, "requirements")
        requirements_stage = "requirements"
    else:
        workflows.begin_requirements(updated)
        requirements_stage = "requirements_gather"
    run_status.running(updated, "DISCOVERING", next_stage=requirements_stage)
    require_binding(state, expected)  # A changed discovery cannot commit the sampled inventory.
    runner.write_json(Path(run_dir) / "state.json", updated)
    cast(dict, state).clear()
    state.update(updated)
    return changes
