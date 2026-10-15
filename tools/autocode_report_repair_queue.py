"""Queue the bounded report-only repair of a rejected completed report.

A provider attempt that finished but whose report was rejected is not run again. The run
queues one report-only repair of the same stage on the same route (``pending_report_repair``),
at most ``report_repair.max_attempts`` times; autocode.execute_report_repair launches it.
This module decides whether a rejected attempt may be repaired and records the pending
repair with its pinned evidence. The ordinary rejection (autocode.reject_completed_stage)
and native planning ownership (autocode_native_calls.PlanningCalls.reject) share it. It
launches nothing, writes no file and imports nothing from the runner or an import cycle.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .autocode_run_state import RunState

try:
    from . import autocode_receipt_repair_context as receipt_repair_context
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
except ImportError:
    import autocode_receipt_repair_context as receipt_repair_context
    import autocode_stray_writes as stray_writes
    import autocode_util as util


class ReportRepairQueued(Exception):
    """A finished request needs report-only repair, never implementation replay."""


def repair_limit(state: RunState) -> int:
    limit = state.get("settings", {}).get("report_repair", {}).get("max_attempts", 0)
    if type(limit) is not int or not 0 <= limit <= 2:
        raise ValueError("report_repair.max_attempts must be an integer from 0 to 2")
    return limit


def queue(state: RunState, record, error, *, completed: Callable[[], bool]) -> bool:
    """Record the repair of a rejected attempt; True while a repair attempt remains.

    Only a terminal, source-pinned report error of a completed attempt qualifies, not a
    transport, stale, permission, guard or stray-write one. The first rejection pins the
    original attempt; a rejected repair is kept as the latest one. Returns False, leaving
    any pending repair untouched, when the attempt does not qualify.
    """
    eligible = (
        isinstance(error, (ValueError, KeyError, RuntimeError))
        and not isinstance(error, (util.Paused, stray_writes.StrayWrites))
        and record.get("exit_code") == 0
        and record.get("source_revision")
        and not record.get("timed_out")
        and not record.get("interrupted")
        and completed()
    )
    pending = state.get("pending_report_repair")
    if not (eligible and repair_limit(state) and (not pending or record.get("report_only"))):
        return False
    if not pending:
        pending = {
            "original": copy.deepcopy(record),
            "attempts": 0,
            "contract_hash": (state.get("goal_contract") or {}).get("hash"),
            "pins": {
                record[key]: util.file_hash(record[key])
                for key in ("events", "before_ref", "after_ref", "schema")
                if record.get(key)
            },
        }
        state["pending_report_repair"] = pending
    else:
        pending["latest_rejected"] = copy.deepcopy(record)
    for key in ("output", "response_text", "events", "schema"):
        if record.get(key) and Path(record[key]).is_file():
            pending["pins"].setdefault(record[key], util.file_hash(record[key]))
    pending["error"] = str(error)
    receipt_repair_context.retain(pending, error)
    return pending["attempts"] < repair_limit(state)
