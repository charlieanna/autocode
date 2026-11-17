"""Chat-facing progress from the status reader's inspected activity and saved attempts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_attempt_ledger as attempt_ledger
    from . import autocode_roles as roles
except ImportError:
    import autocode_attempt_ledger as attempt_ledger
    import autocode_roles as roles


def project(state: RunState, public, *, finished=False, stopped=False) -> list[str]:
    """Display facts only; a finished attempt is not a verified result or retry authority."""
    if public.get("status") != "RUNNING" or (public.get("needs") or {}).get("kind") != "continue":
        return []
    progress = public.get("progress") or {}
    headline = progress.get("headline")
    if not headline:
        return []
    active = attempt_ledger.running_stage(state) or {}
    if active and not finished and not stopped:
        if (public.get("liveness") or {}).get("kind") != "supervised":
            headline = f"{progress.get('stage') or 'Current step'}: activity could not be confirmed."
        else:
            milestone = (state.get("current_task") or {}).get("milestone_id")
            if milestone and roles.job_for_stage(active.get("stage") or "", state) == "builder":
                headline += f" · task {milestone}"
    check = public.get("runner_check") or {}
    if check and not active and not stopped:
        headline = (
            check.get("summary") or "Checking the saved result"
            if (check.get("liveness") or {}).get("kind") == "supervised"
            else "Local check: activity could not be confirmed."
        )
    lines = [headline]
    counts = [
        row["label"]
        for row in (progress.get("tasks") or {}, progress.get("requirements") or {})
        if row.get("total") and row.get("label")
    ]
    if counts:
        lines.append(" · ".join(counts))
    turns = state.get("turns") or []
    since = turns[-1].get("at") if turns else None
    finished_jobs: list[str] = []
    for record in state.get("stages") or []:
        if not isinstance(record, dict) or not record.get("finished_at"):
            continue
        if since and (not record.get("started_at") or record["started_at"] <= since):
            continue
        stage = record.get("stage") or ""
        if not roles.job_for_stage(stage, state):
            continue
        # A runner-owned step (the Orchestrator's dispatch) launches no process, so it has no exit code;
        # reading that absence as a failed exit put "Orchestrator attempt stopped" on a clean run (#1643).
        exited = record.get("exit_code", 0 if record.get("runner_owned") else None)
        # While AutoCode is running on its own (needs.kind == "continue"), rejected reports, report repairs
        # and retried attempts are AutoCode's own machinery (autocode_chat_recoveries.QUIET); they stay in
        # Run details / Show details, never in the active conversation card.
        if record.get("rejected") or record.get("abandoned") or exited != 0:
            continue
        job = roles.screen_name(stage, state)
        if not finished_jobs or finished_jobs[-1] != job:
            finished_jobs.append(job)
    if finished_jobs:
        lines.append("Recent steps: " + " · ".join(f"{job} finished" for job in finished_jobs[-3:]))
    return lines
