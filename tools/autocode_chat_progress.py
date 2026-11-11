"""Chat-facing progress from the status reader's inspected activity and saved attempts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_recovery_history as recovery_history
    from . import autocode_roles as roles
except ImportError:
    import autocode_recovery_history as recovery_history
    import autocode_roles as roles


def project(state: RunState, public, *, finished=False, stopped=False) -> list[str]:
    """Display facts only; a finished attempt is not a verified result or retry authority."""
    if public.get("status") != "RUNNING" or (public.get("needs") or {}).get("kind") != "continue":
        return []
    progress = public.get("progress") or {}
    headline = progress.get("headline")
    if not headline:
        return []
    active = state.get("active_stage") or {}
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
    attempts = []
    newest: dict | None = None
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
        result = (
            "rejected"
            if record.get("rejected")
            else "stopped"
            if exited != 0 or record.get("abandoned")
            else "finished"
        )
        attempts.append((roles.screen_name(stage, state), result))
        newest = record
    if attempts:
        lines.append("Recent steps: " + " · ".join(_steps(attempts)[-3:]))
    # Say why the newest answer was not accepted while the stage tries again, in plain words.
    if newest and newest.get("rejected"):
        why = recovery_history.explain(newest.get("rejection_reason"))
        if why:
            job = roles.screen_name(newest.get("stage") or "", state)
            lines.append(f"Why the last {job} answer was not accepted: {why}")
    return lines


_OUTCOME = {"finished": "finished", "stopped": "stopped", "rejected": "answer not accepted"}


def _steps(attempts: list[tuple[str, str]]) -> list[str]:
    """One entry per run of attempts by the same job, so a retried step reads as one step with its
    outcome and what came before it, not as the same name repeated ("Requirements attempt finished ·
    Requirements attempt finished"). A Requirements answer and its report repairs are one job."""
    groups: list[tuple[str, list[str]]] = []
    for job, result in attempts:
        if groups and groups[-1][0] == job:
            groups[-1][1].append(result)
        else:
            groups.append((job, [result]))
    steps = []
    for job, results in groups:
        text = f"{job} {_OUTCOME[results[-1]]}"
        if len(results) > 1:
            text += f" on attempt {len(results)}"
            earlier = results[:-1]
            notes = [
                f"{count} earlier {noun}{'' if count == 1 else 's'} {verb}"
                for noun, verb, count in (
                    ("answer", "not accepted", earlier.count("rejected")),
                    ("attempt", "stopped", earlier.count("stopped")),
                )
                if count
            ]
            if notes:
                text += f" ({', '.join(notes)})"
        steps.append(text)
    return steps
