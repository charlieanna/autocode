"""The pre-relaunch stale-handoff boundary: a superseded run stops spending (#3288).

A relaunch that follows an admitted stage-level failure/timeout recovery must compare the
source, task, settings, contract and approved scope bound to the failed attempt's launch
with the current ones, and pause PAUSED_STALE_HANDOFF before any provider process is
launched when they moved on outside the run. The run's own retained Builder work at an
unmoved HEAD and the settings transitions the established hold check already admits are
not drift.

``capture_launch_binding`` records what a launch actually used on the attempt record,
inside the one shared launch admission (``autocode_role_launch.admit_launch``), where the
before-snapshot and the pre-launch state exist. ``preserved_launch_binding`` copies that
recorded binding onto a stage-level recovery row — never recomputing it from
then-current values, which would bless drift that landed before recovery admission — and
derives a partial one from the attempt's own recorded evidence when it preserved no
capture. ``admit_relaunch`` is the check itself, run at the same shared admission before
any provider process or allowance charge: it compares the preserved binding through the
established recovery helpers (``_binding``/``_holds``/``_scope``), admits own retained
Builder work per file (``retained_work.own_recovery_source``), and on anything else
records the drift decision in the saved state and pauses before any spend.

This module imports nothing from AutoCode's controller; RunState is for typing only.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_retained_work as retained_work
    from . import autocode_run_records as records
    from . import autocode_run_status as run_status
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_resolver_recovery as resolver_recovery
    import autocode_retained_work as retained_work
    import autocode_run_records as records
    import autocode_run_status as run_status
    import autocode_source_scope as source_scope
    import autocode_util as util

STATUS = "PAUSED_STALE_HANDOFF"
DECISION_EVENT = "stale_handoff_drift"
DISCARD_EVENT = "stale_handoff_discarded"
PAUSED = "stale_handoff_paused"
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


def _refuse(state: RunState, record, bound, moved, run_dir) -> None:
    """Pause the run as stale, recording the decision the core made and its reason."""
    if moved == [UNBOUND]:
        reason = "Stale handoff before relaunch: the failed attempt preserved no launch binding to compare"
    else:
        reason = (
            "Stale handoff before relaunch: changed outside the run since the failed attempt's launch: "
            + ", ".join(moved)
        )
    context = state.get("recovery_context")
    if isinstance(context, dict):
        # The pause is the decision; a person who resumes past it discards this stale handoff
        # (autocode_stop_explanations), so the next relaunch is not refused again on the same binding.
        context[PAUSED] = records.attempt_id(record)
    state.setdefault("user_events", []).append(
        {
            "kind": DECISION_EVENT,
            "actor": "runner",
            "at": util.now(),
            "attempt_id": records.attempt_id(record),
            "compared_binding": copy.deepcopy(bound),
            "changed": list(moved),
        }
    )
    run_status.pause(state, STATUS, reason)
    records.write_json(Path(run_dir) / "state.json", state)
    raise util.Paused(STATUS, reason)


def admit_relaunch(state: RunState, record, workspace, run_dir) -> None:
    """The pre-relaunch drift check, inside the shared launch admission before any spend.

    It fires only where a stage-level failure/timeout recovery governs this launch (its
    preserved ``launch_binding`` rides the recovery row), defers to the existing packet
    admission when the dispatch carries a recovery packet, and skips report-only repairs,
    which keep their own input pins. Any stage may relaunch after such a recovery. The
    current binding is recomputed at this boundary and compared with the preserved one.
    """
    if record.get("report_only") or record.get("dry_run"):
        return
    stage = record.get("stage")
    if stage in ("builder", "resolve", "diagnose") and resolver_recovery._request(state, stage).get("recovery_packet"):
        return  # The packet path owns this dispatch's admission and its exact pause.
    context = state.get("recovery_context")
    if not isinstance(context, dict):
        return
    if context.get(PAUSED):
        # The run paused on this handoff and was resumed past the pause: the stale handoff is
        # discarded, as the pause's explanation says. Record that decision once, then launch.
        if not context.get(DISCARD_EVENT):
            context[DISCARD_EVENT] = util.now()
            state.setdefault("user_events", []).append(
                {
                    "kind": DISCARD_EVENT,
                    "actor": "runner",
                    "at": context[DISCARD_EVENT],
                    "attempt_id": records.attempt_id(record),
                    "paused_attempt_id": context[PAUSED],
                }
            )
        return
    if UNBOUND in context:
        bound = context[UNBOUND]
    else:
        # Old runtimes admitted these recoveries before launch bindings existed.
        # Only the trusted recovery ledger establishes that this is a relaunch;
        # admission-time source/settings must never supply its launch authority.
        attempt = context.get("attempt_id")
        ledgers = (
            state.get("automatic_timeout_recoveries") or [],
            state.get("automatic_capacity_recoveries") or [],
            state.get("automatic_permission_recoveries") or [],
        )
        if not attempt or not any(
            isinstance(row, dict) and row.get("attempt_id") == attempt for ledger in ledgers for row in ledger
        ):
            return
        original = next((row for row in state.get("stages", []) if records.attempt_id(row) == attempt), None)
        bound = preserved_launch_binding(original) if original is not None else None
    moved = _drift(bound, state, source_scope.snapshot(workspace, state)) if isinstance(bound, dict) else [UNBOUND]
    if not moved:
        return
    _refuse(state, record, bound if isinstance(bound, dict) else None, moved, run_dir)
