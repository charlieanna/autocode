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
    from . import autocode_check_refs as check_refs
    from . import autocode_receipt_repair_context as receipt_repair_context
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_check_refs as check_refs
    import autocode_receipt_repair_context as receipt_repair_context
    import autocode_stray_writes as stray_writes
    import autocode_util as util
    import autocode_verification_plan as verification_plan


class ReportRepairQueued(Exception):
    """A finished request needs report-only repair, never implementation replay."""


class ExecutionEvidenceRequired(ValueError):
    """A claimed passing report needs an execution the report-only repair cannot supply.

    Repair may edit prose and reuse evidence a command already produced; it may not rerun
    a command or change an exit code. A PASS report that cites a nonzero check therefore
    goes to a fresh stage attempt, never to the bounded repair loop (#1852).
    """


def require_executed_passes(value, *, passing_claim: bool):
    """Reject a claimed passing report that lists a nonzero check.

    ``check_refs.resolve`` has already replaced each ``check:<n>`` with that check's evidence
    reference. A failed check nothing cites may be omitted by report repair; a cited one needs
    the execution the repair cannot perform, so the caller's stage must run again (#1852).
    """
    if not passing_claim:
        check_refs.require_passing_evidence(value)
        return
    if not value["checks"]:
        raise ValueError(
            "Technically passing validation lacks successful executed checks: list each check you ran, with its exit code"
        )
    failed = [(position, check) for position, check in enumerate(value["checks"], 1) if check["exit_code"]]
    if not failed:
        check_refs.require_passing_evidence(value)
        return
    rows = [*value.get("criterion_results", []), *value.get("milestone_results", [])]
    flow = value.get("end_to_end_result") or {}
    rows += [flow, flow.get("technical_result") or {}]
    cited = any(
        check["evidence_ref"] in (row.get("evidence_refs") or [])
        for _, check in failed
        for row in rows
        if row.get("status") != "FAIL"
    )
    named = ", ".join(f"check {position} (exit {check['exit_code']})" for position, check in failed)
    if cited:
        raise ExecutionEvidenceRequired(
            f"Technically passing validation cites failed {named}: a PASS report requires every listed check to "
            "exit 0, and a report-only repair cannot rerun a command or change its exit code. Re-run the stage "
            "so the command succeeds (a negative assertion must exit 0 when the condition holds) and cite that "
            "execution."
        )
    raise ValueError(
        f"Technically passing validation lists failed {named}: every listed check must exit 0 for PASS. "
        "If nothing cites an exploratory failure, omit it from checks; otherwise rerun the stage so it "
        "succeeds. Never change a recorded exit code."
    )


def repair_limit(state: RunState) -> int:
    limit = state.get("settings", {}).get("report_repair", {}).get("max_attempts", 0)
    if type(limit) is not int or not 0 <= limit <= 2:
        raise ValueError("report_repair.max_attempts must be an integer from 0 to 2")
    return limit


def pending_repair_dispatchable(state: RunState) -> bool:
    """True when this resume still has a bounded report-only repair to dispatch on the owning stage.

    The two conditions it reads are the ones ``execute_report_repair`` refuses: a repair belongs to the
    stage it reports on, and only ``max_attempts`` of them exist. A resume that still holds such a repair
    dispatches a new report of the saved attempt, not another attempt at the failed work, so an
    unchanged-failure hold must not pre-empt it (#1899).
    """
    pending = state.get("pending_report_repair") or {}
    original = pending.get("original")
    return bool(
        isinstance(original, dict)
        and state.get("next_stage") == original.get("stage")
        and pending.get("attempts", 0) < repair_limit(state)
    )


def queue(state: RunState, record, error, *, completed: Callable[[], bool]) -> bool:
    """Record the repair of a rejected attempt; True while a repair attempt remains.

    Only a terminal, source-pinned report error of a completed attempt qualifies, not a
    transport, stale, permission, guard or stray-write one. The first rejection pins the
    original attempt; a rejected repair is kept as the latest one. Returns False, leaving
    any pending repair untouched, when the attempt does not qualify.
    """
    eligible = (
        isinstance(error, (ValueError, KeyError, RuntimeError))
        and not isinstance(
            error,
            (util.Paused, stray_writes.StrayWrites, verification_plan.PlannedCheckFailure, ExecutionEvidenceRequired),
        )
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
