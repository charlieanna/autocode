"""Admission and evidence for revalidating a report held by an internal incident.

This owner grants no retry: it can only inspect the exact pinned report under a
changed validator. Its accepted answer must still pass ordinary final admission.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_operational_block_view as typed_block
    from . import autocode_operational_disposition as disposition
    from . import autocode_operational_outcome as outcome
    from . import autocode_report_correction as correction
    from . import autocode_resolver_human as human
    from . import autocode_startup_timeout_view as startup
    from . import autocode_stop as stop
    from . import autocode_util as util
except ImportError:
    import autocode_operational_block_view as typed_block
    import autocode_operational_disposition as disposition
    import autocode_operational_outcome as outcome
    import autocode_report_correction as correction
    import autocode_resolver_human as human
    import autocode_startup_timeout_view as startup
    import autocode_stop as stop
    import autocode_util as util

STATUSES = frozenset({"PAUSED_REPEATED_FAILURE", "PAUSED_INVALID_OUTPUT", "PAUSED_REPORT_REPAIR_LIMIT"})
ACCOUNTING = frozenset(
    {
        "iteration",
        "active_seconds",
        "milestone_active_seconds",
        "automatic_recoveries_since_resume",
        "consecutive_timeout_recoveries",
        "automatic_timeout_recoveries",
        "automatic_capacity_recoveries",
        "automatic_permission_recoveries",
        "no_progress_batches",
    }
)


def same_place(state: RunState, workspace, run_dir):
    """An observation cannot adopt another checkout or another run's artifacts."""
    try:
        return (
            Path(workspace).resolve() == Path(state["workspace"]).resolve()
            and Path(run_dir).resolve() == Path(state["run_dir"]).resolve()
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def possible(state: RunState):
    """Cheap preflight before a bounded status observation; it grants nothing."""
    pending = state.get("pending_report_repair")
    return bool(
        state.get("status") in STATUSES
        and isinstance(pending, dict)
        and isinstance(pending.get("validation_runtime"), str)
        and pending["validation_runtime"]
        or state.get("status") == "RUNNING"
        and not pending
        and state.get("report_repair_history")
    )


def candidate(state: RunState):
    """Select only the unchanged incident and its own changed-validator report."""
    if not possible(state) or stop.applied_stop(state) or typed_block.receipt(state):
        return None
    if (
        startup.presentation(state)
        or state.get("pending_questions")
        or any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
    ):
        return None
    pause = state.get("pause_intent") or {}
    if not isinstance(pause, dict) or pause and not pause.get("acknowledged_at"):
        return None
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict) or not isinstance(pending.get("original"), dict):
        return None
    previous = pending.get("validation_runtime")
    if not isinstance(previous, str) or not previous or previous == util.runtime_source_digest():
        return None
    rows = disposition.incidents(state)
    if len(rows) != 1 or not disposition.inspectable(state):
        return None
    row = rows[0]
    detail = row.get("receipt") or {}
    binding, evidence = detail.get("evaluation_binding") or {}, detail.get("evidence") or {}
    accounting = binding.get("accounting")
    observed_source = binding.get("observed_source")
    later = disposition.later_events(state, row)
    if (
        not disposition.incident_identity(state, row)
        or evidence.get("status") != state.get("status")
        or evidence.get("stage") != state.get("next_stage")
        or pending["original"].get("stage") != state.get("next_stage")
        or binding.get("next_stage") != state.get("next_stage")
        or not isinstance(observed_source, dict)
        or observed_source.get("revision") != pending["original"].get("source_revision")
        or binding.get("pending_report_repair") != util.digest(pending)
        or binding.get("active_stage") != util.digest(state.get("active_stage"))
        or binding.get("uncertain_artifacts") != util.digest(state.get("uncertain_artifacts"))
        or binding.get("failure_history") != util.digest(state.get("failure_history", {}))
        or later is None
        or any(
            not isinstance(event, dict) or event.get("actor") not in ("runner", "core", "autocode") for event in later
        )
        or not isinstance(accounting, dict)
        or set(accounting) != ACCOUNTING
        or any(value != state.get(key) for key, value in accounting.items())
    ):
        return None
    try:
        if not disposition.same_context(state, binding):
            return None
    except (TypeError, ValueError):
        return None
    return row


def eligible(runtime, state: RunState, workspace, run_dir):
    """Observe the same pinned evidence the locked revalidator will authenticate."""
    row = candidate(state)
    if not same_place(state, workspace, run_dir):
        return False
    run_dir = Path(run_dir)
    if row is None or any(human.pending_interruptions(run_dir).values()) or stop.pending_stop(run_dir) is not None:
        return False
    return correction.authenticate(runtime, state, workspace, state["pending_report_repair"]) is not None


def observe(runtime, state: RunState, workspace, run_dir):
    """A bounded reader may offer only an owner the locked writer rechecks."""
    if not same_place(state, workspace, run_dir):
        return None
    run_dir = Path(run_dir)
    if not possible(state) or any(human.pending_interruptions(run_dir).values()) or stop.pending_stop(run_dir):
        return None
    if eligible(runtime, state, workspace, run_dir):
        return "pending"
    if state.get("status") != "RUNNING" or typed_block.receipt(state) or stop.applied_stop(state):
        return None
    rows = disposition.incidents(state)
    if len(rows) != 1 or not disposition.inspectable(state):
        return None
    row = rows[0]
    try:
        if not disposition.same_context(state, (row.get("receipt") or {}).get("evaluation_binding") or {}):
            return None
    except (TypeError, ValueError):
        return None
    checked = disposition.checked_recovery(state, row)
    if (
        not checked
        or checked.get("selection") != "retained_report"
        or validate(runtime, None, state, run_dir, workspace, None, row, checked, final=True) != checked.get("result")
    ):
        return None
    return "accepted"


def validate(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    """Recheck the applied record and the original incident's exact evidence."""
    if (
        not same_place(state, workspace, run_dir)
        or stop.applied_stop(state)
        or stop.pending_stop(Path(run_dir))
        or typed_block.receipt(state)
        or any(human.pending_interruptions(run_dir).values())
    ):
        return None
    proof = outcome.proof(state, observed)
    if not isinstance(proof, dict) or set(proof) != {"incident", "pending", "runtime"}:
        return None
    incident = disposition.incident_identity(state, row)
    pending = proof["pending"]
    detail = row.get("receipt") or {}
    binding, evidence = detail.get("evaluation_binding") or {}, detail.get("evidence") or {}
    if (
        incident != proof["incident"]
        or not isinstance(pending, dict)
        or binding.get("pending_report_repair") != util.digest(pending)
        or evidence.get("status") not in STATUSES
        or proof["runtime"] != util.runtime_source_digest()
        or not isinstance(pending.get("validation_runtime"), str)
        or pending["validation_runtime"] == proof["runtime"]
        or state.get("pending_report_repair")
        or any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
    ):
        return None
    original = pending.get("original") or {}
    source = binding.get("observed_source")
    if (
        not isinstance(original, dict)
        or evidence.get("stage") != original.get("stage")
        or binding.get("next_stage") != original.get("stage")
        or not isinstance(source, dict)
        or source.get("revision") != original.get("source_revision")
    ):
        return None
    old = copy.deepcopy(state)
    old["next_stage"] = original.get("stage")
    old["pending_report_repair"] = pending
    if correction.authenticate(runner, old, workspace, pending) is None:
        return None
    latest = pending.get("latest_rejected") or original
    records = [
        item
        for item in state.get("report_repair_history") or []
        if isinstance(item, dict)
        and item.get("result") == "revalidated"
        and item.get("original_output") == original.get("output")
    ]
    if len(records) != 1:
        return None
    saved = records[0]
    repair = saved.get("repair")
    if (
        not isinstance(repair, dict)
        or not repair.get("report_revalidated")
        or any(
            repair.get(key) != latest.get(key)
            for key in ("stage", "role", "iteration", "events", "schema", "source_revision")
        )
        or repair.get("applied_original_events") != original.get("events")
        or saved.get("attempts") != pending.get("attempts")
        or not isinstance(repair.get("output"), str)
        or not Path(repair["output"]).is_file()
        or runner.support.file_hash(repair["output"]) != saved.get("output_hash")
        or repair not in (state.get("stages") or [])
    ):
        return None
    return outcome.answer(
        state,
        proof,
        {"report_revalidated": saved},
        fields=(
            "settings",
            "current_task",
            "goal_contract",
            "next_stage",
            "pending_report_repair",
            "pending_questions",
            "user_events",
            "answers",
            "brief_feedback",
            "failure_history",
            "active_stage",
            "uncertain_artifacts",
        ),
        members=(("report_repair_history", saved), ("stages", repair)),
    )
