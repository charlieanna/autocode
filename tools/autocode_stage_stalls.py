"""One stage's stopped attempts on unchanged source, counted across the reviews that re-issue it.

A stopped, non-terminal attempt (a stage or idle timeout) is archived and the run continues
(autocode_stage_recovery.automatically_recover_timed_out_stage). The failure streak that bounds
those recoveries (autocode_recovery_accounting) ends at any valid saved stage. A Tester that
stalls is re-issued by the Completion Reviewer, whose valid report ends the streak, so the Tester
can stall on the same source forever. A live run (2026-11-06) archived six Tester attempts that
way, three on each of two revisions, and never reached the Investigator, which runs only on a
non-convergence pause (autocode_stuck_job.STATUSES).

So this counts one stage's recoveries on one source revision since that stage last saved a valid
report. When there are ``REPEATS`` of them and another stage's valid report ended the streak in
between, the recovery pauses ``PAUSED_REPEATED_FAILURE``, the same failure repeated on unchanged
source, and the Investigator diagnoses it before anyone is asked. Tester and completion-review
retries also take this boundary in an unbroken streak: another identical review would repeat the
same checks without a diagnosis. Other stages keep the streak's own bound. A changed source or a
valid report from the stage starts the count again.
Pure: reads the run state it is given.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_streak as streak
except ImportError:
    import autocode_recovery_streak as streak

REPEATS = 2
RESTARTS_PER_SOURCE = 2
CANNOT_LAUNCH = "the report repair cannot launch from its saved inputs"
STATUS = "PAUSED_REPEATED_FAILURE"


def _stage(record) -> str | None:
    return (record.get("original_stage") or record.get("stage")) if isinstance(record, dict) else None


def _saved_since(state: RunState, stage: str):
    saved = [
        streak.timestamp(row["finished_at"])
        for row in state.get("stages") or []
        if _stage(row) == stage and streak.saved_checkpoint(row)
    ]
    return max(saved, default=None)


def repeated(state: RunState, stage: str, revision) -> list[dict]:
    """The recoveries of ``stage`` on ``revision`` since ``stage`` last saved a valid report."""
    since = _saved_since(state, stage)
    rows = []
    for row in state.get("automatic_timeout_recoveries") or []:
        at = streak.timestamp(row.get("at")) if isinstance(row, dict) else None
        if _stage(row) != stage or row.get("source_revision") != revision or at is None:
            continue
        if row.get("timeout_kind") == "person":
            continue  # a person's stop (#3263, #3259) is not the stage stalling
        if since is None or at > since:
            rows.append(row)
    return rows


def unlaunchable_repairs(state: RunState, stage: str, revision) -> list[dict]:
    """Archived repairs of ``stage`` on ``revision`` that could not launch since ``stage`` last saved a valid report."""
    since = _saved_since(state, stage)
    rows = []
    for row in state.get("report_repair_archive") or []:
        if not isinstance(row, dict) or CANNOT_LAUNCH not in str(row.get("reason")):
            continue
        original = ((row.get("repair") or {}).get("original")) or {}
        if _stage(original) != stage or original.get("source_revision") != revision:
            continue
        at = streak.timestamp(row.get("at"))
        if since is not None and at is not None and at <= since:
            continue
        rows.append(row)
    return rows


def exhausted_unlaunchable_pending(state: RunState) -> bool:
    """True when a pre-reconciliation PAUSED_REPEATED_FAILURE hold kept its spent unlaunchable repair pending."""
    if state.get("status") != STATUS or state.get("active_stage"):
        return False
    pending = state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return False
    original = pending.get("original")
    stage = _stage(original)
    revision = original.get("source_revision") if isinstance(original, dict) else None
    if not stage or stage != state.get("next_stage") or not revision or pending.get("attempts", 0) != 0:
        return False
    return len(unlaunchable_repairs(state, stage, revision)) >= RESTARTS_PER_SOURCE


def _repair_events(repair) -> tuple[str | None, ...]:
    if not isinstance(repair, dict):
        return ()
    return (
        (repair.get("latest_rejected") or {}).get("events"),
        (repair.get("original") or {}).get("events"),
    )


def escapes_streak(state: RunState, rows: list[dict]) -> bool:
    """Repeated reviews require diagnosis even when no intervening report reset the streak.

    ``consecutive_timeout_recoveries`` already counts this recovery; when it covers every row, the
    stops are one unbroken streak. Writers keep that limit; unchanged reviewers reach the
    Investigator before spending another stage/tool allowance on the same verification."""
    return len(rows) >= REPEATS and (
        all(_stage(row) in ("tester", "review_completion", "review_checkpoint") for row in rows)
        or int(state.get("consecutive_timeout_recoveries") or 0) < len(rows)
    )


def retry_entry(state: RunState, record: dict) -> dict | None:
    """Match the most recent archived timeout or unlaunchable-repair hold to its failure ledger entry."""
    stage = _stage(record)
    revision = record.get("source_revision")
    events = record.get("events")
    if not stage or stage != state.get("next_stage") or not revision or not events:
        return None
    if record.get("timed_out"):
        rows = repeated(state, stage, revision)
        if not escapes_streak(state, rows) or rows[-1].get("events") != events:
            return None
    else:
        repairs = unlaunchable_repairs(state, stage, revision)
        matched_archived = len(repairs) > RESTARTS_PER_SOURCE and events in _repair_events(repairs[-1].get("repair"))
        matched_pending = exhausted_unlaunchable_pending(state) and events in _repair_events(
            state.get("pending_report_repair")
        )
        if not (matched_archived or matched_pending):
            return None
    entry = (state.get("failure_history") or {}).get(record.get("failure_key"))
    identity = (entry or {}).get("identity") or {}
    if (
        identity.get("stage") != stage
        or identity.get("artifact_hash") != revision
        or record.get("failure_attempt") not in (entry or {}).get("attempts", [])
    ):
        return None
    return entry


def reason(stage_name: str, rows: list[dict]) -> str:
    kinds = sorted({str(row.get("timeout_kind") or "stage") for row in rows})
    revision = str(rows[-1].get("source_revision") or "")[:8]
    return (
        f"{stage_name} stopped {len(rows)} times on unchanged source {revision} "
        f"({', '.join(kinds)} timeout) without a valid report; the same failure repeated, "
        "so another identical attempt is not launched"
    )
