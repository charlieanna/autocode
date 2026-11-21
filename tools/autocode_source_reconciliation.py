"""Recheck a source-read hold without changing authority, routes or allowances.

The read-only offer observes source and supervision. The existing resume action
repeats those checks under its run lock and reconciles retained artifacts through
their existing owners; a source read never proves a model result or a test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_catalog_context as catalog_context
    from . import autocode_liveness as liveness
    from . import autocode_repo_catalog as repo_catalog
    from . import autocode_source_scope as source_scope
    from . import autocode_stop as stop
    from . import autocode_util as util
except ImportError:
    import autocode_catalog_context as catalog_context
    import autocode_liveness as liveness
    import autocode_repo_catalog as repo_catalog
    import autocode_source_scope as source_scope
    import autocode_stop as stop
    import autocode_util as util

STATUS = "PAUSED_SOURCE_UNAVAILABLE"
NOTICE = (
    "AutoCode could not read the task's source. Saved work and evidence are kept; "
    "no source snapshot was accepted. AutoCode will recheck access before continuing."
)


def eligible(state: RunState, inspection, *, controller=None, stop_pending=False):
    """Check the in-memory stopped boundary before attempting any source read."""
    if (
        state.get("status") != STATUS
        or state.get("ui_run")
        or (state.get("execution") or {}).get("mode") == "durable_local"
        or state.get("active_runner_check")
        or state.get("uncertain_artifacts")
        or stop_pending
        or stop.applied_stop(state) is not None
        or (state.get("pause_intent") and not state["pause_intent"].get("acknowledged_at"))
        or not isinstance(controller, dict)
        or controller.get("kind") not in ("ended", "boundary")
    ):
        return None
    active = state.get("active_stage")
    if active:
        fresh = liveness.classify(active.get("supervision"), inspection)
        receipt = fresh.get("receipt") or {}
        if (
            fresh["kind"] not in ("stopped", "interrupted")
            or receipt.get("phase") not in ("stopped", "discharged")
            or receipt.get("cleanup_error") is not None
            or not all(fresh[role]["checked"] and fresh[role]["alive"] is False for role in liveness.ROLES)
        ):
            return None
    return True


def offer(state: RunState, workspace, inspection, *, controller=None, stop_pending=False):
    """One current, read-only source observation; no retry or result is authorized here."""
    if not eligible(state, inspection, controller=controller, stop_pending=stop_pending):
        return None
    if not readable(workspace, state):
        return None
    return {
        "kind": "continue",
        "action": "--resume-paused",
        "reason": "Source access is readable again. AutoCode will reconcile saved work under the existing limits.",
    }


def readable(workspace, state: RunState) -> bool:
    """Whether every source a run reads can be read now: its workspace and discovery under the configured roots.

    PAUSED_SOURCE_UNAVAILABLE has both causes (a Git read of the workspace, and discovery that cannot see every
    checkout under the shared source roots, autocode_catalog_context.DiscoveryIncomplete), so the offer observes
    both rather than the one it was written for. Otherwise a discovery stop is offered as readable, and resume
    stops at the same place again.
    """
    try:
        source_scope.snapshot(workspace, state)
        return catalog_problem(state) is None
    except (util.Paused, OSError, ValueError, RuntimeError, TypeError):
        return False


def catalog_problem(state: RunState):
    """Observe the same pinned inventory for the offer and the locked resume."""
    discovery = repo_catalog.discover()
    if stopped := repo_catalog.blocking(discovery):
        return catalog_context.DiscoveryIncomplete(stopped, discovery.get("catalog_roots") or ())
    saved = catalog_context.saved_inventory(state)
    if changes := catalog_context.binding_changes(saved, discovery):
        return catalog_context.CatalogChanged(changes)
    return None


def prepare(runner, state: RunState, workspace):
    """Writer's source recheck before ordinary resume bookkeeping; never renew an allowance."""
    if state.get("status") != STATUS or getattr(runner, "native_calls", None) is not None:
        return None
    active = state.get("active_stage")
    if active:
        runner.assert_stage_stopped(active)
    current = source_scope.snapshot(workspace, state, base_snapshot=runner.support.snapshot)
    if problem := catalog_problem(state):
        raise problem
    note = {
        "kind": "source_access_recovered",
        "actor": "runner",
        "at": runner.now(),
        "reason": state.get("stop_reason"),
        "source_revision": current["revision"],
        "attempt_id": runner.attempt_id(active) if active else None,
        "allowances_renewed": False,
    }
    state.setdefault("reconciliation_notes", []).append(note)
    return note


def presentation(state: RunState, need):
    if state.get("status") != STATUS:
        return None
    ready = (need or {}).get("kind") == "continue"
    catalog_recovery = str((need or {}).get("action") or "").startswith("--reconcile-only catalog-v1:")
    changed = str(state.get("stop_reason") or "").startswith("The task's source catalog changed:")
    return {
        "title": "Updating the task source"
        if catalog_recovery
        else "Recovering source access"
        if ready
        else "Task source changed"
        if changed
        else "AutoCode cannot read the task source",
        "category": "recovery" if ready else "blocked",
        "what_happened": (
            "Source bindings changed. AutoCode is recording the new inventory and revisiting Requirements and the plan."
            if catalog_recovery
            else "Source access is readable again. AutoCode is reconciling the saved attempt; "
            "model choices, limits, usage and failed attempts are retained."
            if ready
            else str(state.get("stop_reason")) + ". The original inventory remains recorded."
            if changed
            else NOTICE
        ),
    }
