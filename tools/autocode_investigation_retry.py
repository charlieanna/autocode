"""Unverified artifact leads for an explicitly authorized Investigator retry.

This projection reads metadata and the saved event file's tail (what the attempt read and ran). It grants no retry or evidence authority;
source/configuration admission and reproduction probes remain with their owners.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

try:
    from . import autocode_investigation_progress as progress
except ImportError:
    import autocode_investigation_progress as progress

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def context(state: RunState) -> dict | None:
    failure = state.get("job_failure") or {}
    recovery = state.get("recovery_context") or {}
    authorization = state.get("job_retry_authorization") or {}
    stage = "investigate_bug"
    attempt = failure.get("attempt_id")
    events = recovery.get("events")
    archive = failure.get("archive")
    if (
        failure.get("stage") != stage
        or recovery.get("kind") != "job_failure"
        or recovery.get("stage") != stage
        or not isinstance(attempt, str)
        or recovery.get("attempt_id") != attempt
        or authorization.get("stage") != stage
        or authorization.get("consumed") is not False
        or not failure.get("job_retry_token")
        or authorization.get("token") != failure["job_retry_token"]
        or not failure.get("source_identity")
        or not isinstance(events, str)
        or not isinstance(archive, str)
        or Path(events).parent != Path(archive)
        or Path(events).suffix != ".jsonl"
        or Path(events).stem != attempt.rsplit("/", 1)[-1]
    ):
        return None
    explored = progress.summarize(progress.tail_rows(events), state.get("workspace"))
    return {
        "attempt_id": attempt,
        "source_identity": failure["source_identity"],
        "reason": recovery.get("reason"),
        "previous_prompt": str(Path(events).with_suffix(".prompt.md")),
        "previous_events": events,
        "evidence_status": "unverified_leads_only",
        # What the stopped attempt read and ran, from its saved events: leads to skip, not findings.
        "stop_kind": recovery.get("timeout_kind"),
        **explored,
    }
