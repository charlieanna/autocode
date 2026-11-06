"""One stage's stopped attempts on unchanged source, counted across the reviews that re-issue it.

A stopped, non-terminal attempt (a stage or idle timeout) is archived and the run continues
(autocode_stage_recovery.automatically_recover_timed_out_stage). The failure streak that bounds
those recoveries (autocode_recovery_accounting) ends at any valid saved stage. A Tester that
stalls is re-issued by the Completion Reviewer, whose valid report ends the streak, so the Tester
can stall on the same source forever. A live run (2026-11-06) archived six Tester attempts that
way, three on each of two revisions, and never reached the Investigator, which runs only on a
non-convergence pause (autocode_stuck_job.STATUSES).

So this counts one stage's recoveries on one source revision since that stage last saved a valid
report. At ``REPEATS`` the recovery pauses ``PAUSED_REPEATED_FAILURE``, the same failure repeated
on unchanged source, and the Investigator diagnoses it before anyone is asked. A changed source or
a valid report from the stage starts the count again. Pure: reads the run state it is given.
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
STATUS = "PAUSED_REPEATED_FAILURE"


def _stage(record) -> str | None:
    return (record.get("original_stage") or record.get("stage")) if isinstance(record, dict) else None


def repeated(state: RunState, stage: str, revision) -> list[dict]:
    """The recoveries of ``stage`` on ``revision`` since ``stage`` last saved a valid report."""
    saved = [
        streak.timestamp(row["finished_at"])
        for row in state.get("stages") or []
        if _stage(row) == stage and streak.saved_checkpoint(row)
    ]
    since = max(saved, default=None)
    rows = []
    for row in state.get("automatic_timeout_recoveries") or []:
        at = streak.timestamp(row.get("at")) if isinstance(row, dict) else None
        if _stage(row) != stage or row.get("source_revision") != revision or at is None:
            continue
        if since is None or at > since:
            rows.append(row)
    return rows


def reason(stage_name: str, rows: list[dict]) -> str:
    kinds = sorted({str(row.get("timeout_kind") or "stage") for row in rows})
    revision = str(rows[-1].get("source_revision") or "")[:8]
    return (
        f"{stage_name} stopped {len(rows)} times on unchanged source {revision} "
        f"({', '.join(kinds)} timeout) without a valid report; the same failure repeated, "
        "so another identical attempt is not launched"
    )
