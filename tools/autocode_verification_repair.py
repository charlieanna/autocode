"""Retire an unlaunched rejected Tester report only with corrected verification inputs.

Preparation is read-only. The locked settings writer commits the archive only
after every configuration check succeeds; no rejected report is ever adopted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_pause_authority as pause_authority
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_util as util
except ImportError:
    import autocode_pause_authority as pause_authority
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_util as util

FIELDS = ("test_command", "regression_command", "test_report")
BLOCKED = ("active_stage", "uncertain_artifacts", "active_runner_check", "runner_check")
BOUND = (*BLOCKED, "status", "next_stage", "goal_contract", "current_task", "pending_report_repair", "stages")
IDENTITY = (
    "stage",
    "iteration",
    "role",
    "output",
    "events",
    "source_revision",
    "contract_hash",
    "contract_revision",
    "task_id",
)
REASON = "Verification inputs changed at an idle Tester checkpoint; its rejected report repair is obsolete"


def _original(state: RunState):
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict) or "latest_rejected" in pending or pending.get("truncated_output"):
        return None
    original = pending.get("original")
    contract = state.get("goal_contract") or {}
    rows = [
        row
        for row in state.get("stages") or []
        if isinstance(row, dict) and (not row.get("runner_owned") or row.get("report_only"))
    ]
    attempts = pending.get("attempts")
    limit = ((state.get("settings") or {}).get("report_repair") or {}).get("max_attempts")
    if (
        not isinstance(original, dict)
        or not rows
        or state.get("next_stage") not in ("sol", "astra_checkpoint")
        or original.get("stage") != state.get("next_stage")
        or any(rows[-1].get(key) != original.get(key) for key in IDENTITY)
        or not original.get("rejected")
        or not rows[-1].get("rejected")
        or not original.get("accounted")
        or original.get("exit_code") != 0
        or any(original.get(key) for key in ("report_only", "interrupted", "timed_out", "dry_run"))
        or not original.get("finished_at")
        or not original.get("source_revision")
        or not contract.get("hash")
        or pending.get("contract_hash") != contract["hash"]
        or original.get("contract_hash") != contract["hash"]
        or original.get("contract_revision") != contract.get("revision")
        or original.get("task_id", "") != (state.get("current_task") or {}).get("id", "")
        or pending.get("original_error", pending.get("error")) != original.get("rejection_reason")
        or type(attempts) is not int
        or type(limit) is not int
        or not 1 <= limit <= 2
        or not 0 <= attempts <= limit
    ):
        return None
    # The counter advances before run_role. Ordered provider history, not zero,
    # establishes that no report-only repair has subsequently launched.
    if rows[-1].get("report_only"):
        return None
    return original


def _idle(state: RunState):
    interrupted = pause_authority.interrupted(state)
    return (
        str(state.get("status", "")).startswith("PAUSED_")
        and not any(state.get(key) for key in BLOCKED)
        and not state.get("pending_questions")
        and not state.get("resolver_human_proposal")
        and not state.get("resolver_human_request")
        and not (interrupted and pause_authority.operational(interrupted["status"]))
        and not any(row.get("kind") == "stop" for row in state.get("applied_interventions") or [])
    )


def _settled(original):
    observed = processes.recorded_worker_state(original)
    if not observed["checked"] or observed["alive"] is not False:
        return False
    metadata = original.get("supervision")
    if metadata:
        observed = supervision.observe(metadata)
        if not all(
            isinstance(observed.get(role), dict)
            and observed[role].get("checked")
            and observed[role].get("alive") is False
            for role in ("owner", "keeper", "provider")
        ):
            return False
    return True


def prepare(state: RunState, previous, current):
    """Bind a possible retirement without mutating the checkpoint or its counters."""
    if any(
        values.get(key) is not None and not isinstance(values[key], str)
        for values in (previous, current)
        for key in FIELDS
    ):
        return None
    if not _idle(state) or not any(
        (previous.get(key) or "").strip() != (current.get(key) or "").strip() for key in FIELDS
    ):
        return None
    original = _original(state)
    if original is None:
        return None
    pins = (state.get("pending_report_repair") or {}).get("pins")
    if not isinstance(pins, dict) or any(
        not original.get(key) or original[key] not in pins
        for key in ("output", "events", "schema", "before_ref", "after_ref")
    ):
        return None
    try:
        if any(not Path(path).is_file() or util.file_hash(path) != expected for path, expected in pins.items()):
            return None
        if not _settled(original):
            return None
    except (OSError, ValueError, KeyError, TypeError, processes.ProcessError):
        return None
    return {
        "binding": util.digest({key: state.get(key) for key in BOUND}),
        "current": {key: current.get(key) for key in FIELDS},
    }


def commit(state: RunState, settings, prepared):
    """Archive obsolete repair and proof beside the successfully validated settings."""
    if prepared is None:
        return
    if (
        prepared.get("binding") != util.digest({key: state.get(key) for key in BOUND})
        or prepared.get("current") != {key: (settings.get("regression") or {}).get(key) for key in FIELDS}
        or not _idle(state)
        or _original(state) is None
    ):
        raise ValueError("The pending Tester report changed before verification inputs could be saved")
    at = util.now()
    state.setdefault("report_repair_archive", []).append(
        {
            "kind": "verification_inputs_changed",
            "at": at,
            "reason": REASON,
            "repair": state.pop("pending_report_repair"),
            "regression_proof": state.pop("regression_proof", None),
        }
    )
    state.setdefault("user_events", []).append(
        {
            "kind": "verification_report_repair_retired",
            "actor": "runner",
            "at": at,
            "stage": state["next_stage"],
            "reason": REASON,
        }
    )
