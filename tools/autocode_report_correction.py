"""Whether a finished stage's saved report is still valid evidence that needs only a report correction (#3868).

A rejected report is kept with its pinned evidence (``autocode_report_repair_queue``). After the
validator changes, resume first re-checks that exact report (``autocode_report_revalidation``). When the
report still authenticates (its stage, source revision, pins, contract and handoff binding are those it
was produced for) and the updated validator rejects only its contents, the stage's execution evidence
is still valid: the report needs a correction, not a new original-code baseline, regression proof or
full attempt of the stage. This module is the one place that decides that, for the resume dispatch
(``autocode_action_recovery``) and for an archived repair's recovery (``autocode_stage_recovery``):

- ``authenticate``: the saved report is still the evidence it was;
- ``superseded``: a later attempt of the same stage replaced it;
- ``ARCHIVE_KINDS``: which ``report_repair_archive`` reasons may bring a repair back, and on which rule;
- ``resume``: revalidate under the current runtime, and renew a bounded report-only correction when this
  runtime's revalidation, made now, rejected the authenticated report.

A stored revalidation row never qualifies on its own: the rejection must be made by the current runtime
during this resume, so one validator update grants at most one renewal. The decision is the runner's,
recorded with its reason; a person is never asked. It imports only lower-level modules and reaches the
revalidation through the runner it is given.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_streak as streak
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_report_revalidation_binding as revalidation_binding
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_stalls as stage_stalls
    from . import autocode_util as util
except ImportError:
    import autocode_recovery_streak as streak
    import autocode_report_repair_queue as report_repair_queue
    import autocode_report_revalidation_binding as revalidation_binding
    import autocode_source_scope as source_scope
    import autocode_stage_stalls as stage_stalls
    import autocode_util as util


SPENT = "Explicit fresh execution retry after bounded report failures"
UNLAUNCHABLE = f"Unchanged repeated failure hold: {stage_stalls.CANNOT_LAUNCH}"
# The archive reasons whose repair may come back, by kind. A spent repair returns only for a report
# correction (``resume``); an unlaunchable one only when it now fits the launch bounds
# (``autocode_stage_recovery.restore_launchable_unlaunchable_repair``), and only if it never launched.
ARCHIVE_KINDS = {SPENT: "spent", UNLAUNCHABLE: "unlaunchable"}
CORRECTION = "Authenticated {stage} report needs only a report correction"


def authenticate(runtime, state: RunState, workspace, pending, *, stage=None):
    """The rejected record ``pending`` would revalidate, when it is still the evidence it was; else None.

    ``stage`` is the stage that must be next (default: the repair's own stage). The record must be the
    same completed attempt (or a report-only repair of it) on the same iteration, source revision and
    contract, every pinned file must be unchanged, and the original handoff binding must hold.
    """
    if not isinstance(pending, dict) or any(
        state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts")
    ):
        return None
    original = pending.get("original") or {}
    if not isinstance(original, dict):
        return None
    expected_stage = stage or original.get("stage")
    if not original.get("stage") or state.get("next_stage") != expected_stage or not original.get("source_revision"):
        return None
    if not revalidation_binding.matches(state, original["stage"], pending.get("revalidation_context"), original):
        return None
    record = pending.get("latest_rejected") or original
    pins = pending.get("pins") or {}
    if not isinstance(pins, dict):
        return None
    if not isinstance(record, dict) or not record.get("rejected") or record.get("exit_code") != 0:
        return None
    if record is not original and (
        not record.get("report_only")
        or record.get("original_stage", str(record.get("stage", "")).removesuffix("_report_repair"))
        != original["stage"]
        or record.get("iteration") != original.get("iteration")
        or record.get("source_revision") != original.get("source_revision")
        or record.get("contract_hash") != original.get("contract_hash")
    ):
        return None
    if any(record.get(key) for key in ("timed_out", "interrupted", "truncated_output", "first_reply_budget")):
        return None
    required = [(original, key) for key in ("output", "events", "schema", "before_ref", "after_ref")]
    required.extend((record, key) for key in ("output", "events", "schema"))
    if (
        any(not isinstance(row.get(key), str) or not row[key] or row[key] not in pins for row, key in required)
        or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")
        or source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)["revision"]
        != original["source_revision"]
        or any(not Path(path).is_file() or runtime.support.file_hash(path) != digest for path, digest in pins.items())
        or not runtime.stage_completed(state, record)
    ):
        return None
    return record


def superseded(state: RunState, repair, since) -> bool:
    """True when an attempt of the repair's stage started after ``since`` or is running now.

    Report-only repairs and runner-owned steps do not supersede: only a new attempt of the stage itself
    produces evidence that replaces the saved report. Without a readable ``since`` only a running attempt
    can be judged.
    """
    original = (repair or {}).get("original") or {}
    stage = original.get("stage")
    active = state.get("active_stage")
    if isinstance(active, dict) and active.get("stage") == stage:
        return True
    after = streak.timestamp(since)
    return after is not None and any(
        isinstance(row, dict)
        and row.get("stage") == stage
        and not row.get("report_only")
        and not row.get("runner_owned")
        and (started := streak.timestamp(row.get("started_at"))) is not None
        and started > after
        for row in state.get("stages") or []
    )


def archived(state: RunState, kind):
    """The last ``report_repair_archive`` entry when its repair of the next stage may return as ``kind``."""
    archive = state.get("report_repair_archive")
    if state.get("pending_report_repair") or state.get("active_stage") or not isinstance(archive, list) or not archive:
        return None
    entry = archive[-1]
    repair = entry.get("repair") if isinstance(entry, dict) else None
    if not isinstance(repair, dict):
        return None
    original = repair.get("original")
    if (
        not isinstance(original, dict)
        or ARCHIVE_KINDS.get(entry.get("reason")) != kind
        or original.get("stage") != state.get("next_stage")
        # An unlaunchable repair never ran; a spent one did, at least once.
        or (repair.get("attempts") != 0 if kind == "unlaunchable" else not repair.get("attempts"))
        or streak.timestamp(entry.get("at")) is None
        or superseded(state, repair, entry.get("at"))
    ):
        return None
    return entry


def _latest_record(pending):
    record = pending.get("latest_rejected") or pending.get("original") or {}
    return record.get("started_at") if isinstance(record, dict) else None


def _unrestore(state: RunState, entry, pending):
    """Put a repair restored from the archive back exactly where it was."""
    if entry is not None and state.get("pending_report_repair") is pending:
        state.pop("pending_report_repair")
        state.setdefault("report_repair_archive", []).append(entry)


def resume(runner, state: RunState, run_dir, workspace):
    """Settle a saved rejected report on resume: "accepted", "corrected" or None (today's path).

    "accepted": the current validator accepted the pinned report; it was applied without a launch.
    "corrected": it authenticated and the current runtime rejected only its contents just now. The repair
    (restored from a spent archive entry if needed) carries the new diagnostic, its bounded allowance is
    renewed by the runner with the lifetime count kept, and the decision is recorded. None changes nothing.
    """
    revalidator = getattr(runner, "report_revalidation", None)
    if revalidator is None:
        return None
    entry = None
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        entry = archived(state, "spent")
        if entry is None:
            return None
        pending = state["pending_report_repair"] = state["report_repair_archive"].pop()["repair"]
    seen = len(pending.get("revalidations") or [])
    try:
        if revalidator.revalidate(runner, state, run_dir, workspace):
            return "accepted"
    except BaseException:
        _unrestore(state, entry, pending)
        raise
    rows = (pending.get("revalidations") or [])[seen:]
    if (
        not rows
        or rows[-1].get("result") != "rejected"
        or rows[-1].get("runtime") != util.runtime_source_digest()
        or (entry is None and superseded(state, pending, _latest_record(pending)))
    ):
        _unrestore(state, entry, pending)
        return None
    rejection = rows[-1]
    original = pending["original"]
    pending["error"] = rejection["reason"]
    at = runner.now()
    report_repair_queue.reset_for_resume(state, now=lambda: at, actor="runner")
    reason = (
        CORRECTION.format(stage=original["stage"])
        + ": its source revision, pins, contract and handoff binding are unchanged and the current validator "
        "rejected only its contents, so a bounded report-only repair runs with the new diagnostic and the "
        "original execution evidence; no new baseline, regression proof or full attempt."
    )
    note = {
        "at": at,
        "stage": original["stage"],
        "iteration": original.get("iteration"),
        "actor": "runner",
        "reason": reason,
        "revalidation": copy.deepcopy(rejection),
        "original_output": original.get("output"),
        "restored_from_archive": entry is not None,
    }
    state.setdefault("reconciliation_notes", []).append(note)
    state.setdefault("user_events", []).append(
        {"kind": "report_correction", "actor": "runner", "at": at, "stage": original["stage"], "reason": reason}
    )
    runner.write_json(Path(run_dir) / "state.json", state)
    return "corrected"
