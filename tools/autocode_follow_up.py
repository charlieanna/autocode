"""A follow-up message to a finished run: the next turn of the same conversation (issue #51).

``autocode --run-dir R --follow-up TEXT`` on a finished run records TEXT as a new turn and
reopens the run: the job recognizer reads TEXT (with the earlier turn as context) and picks
the next workflow, in the same run directory. "Review PR #184" then "Fix them." is one
conversation: the review's findings carry forward, and a build that acts on them is planned
from them, the way a bug fix is planned from its diagnosis (autocode_bug_job), so
requirements gathering is skipped. Plan review and the user's approval still apply.

This module is pure over the run state and the review report file. It imports nothing from
the runner.

State keys written here:
    brief_feedback, user_events: the same user-input receipt in both existing ledgers.
        Planning receives its exact text and ID through the standard handoff; the
        contract guard accepts that ID as user_feedback for declared revisions.
        Recording a follow-up does not grant plan approval or recovery allowance.
    turns: [{"at", "say", "previous": {"task", "workflow", "status", "completed_at", "first_stage",
             "review": {"report_path", "change_under_review", "change_patch", "verdict",
                        "blocking": [...], "advisory": [...]} or absent}}]
        One entry per follow-up. Read by autocode_workflows.follow_up (the recognizer judges the
        newest message), by review_findings (the Planner's handoff) and by the status view.
    task: rewritten to the follow-up, followed by the earlier request as context, so every
        later stage reads what the user now wants. The original request stays in turns.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

try:
    from . import autocode_workflows as workflows, autocode_contract_identity as identity
except ImportError:
    import autocode_workflows as workflows
    import autocode_contract_identity as identity

FINDING_FIELDS = ("id", "severity", "file", "lines", "summary", "evidence")


def accept(state: dict, text: str, workspace, now: str) -> None:
    """Record ``text`` as the next turn and reopen the finished run to recognize it.

    Raises ValueError, leaving ``state`` unchanged, when the run is not finished or the
    message is empty: a running or paused run takes --feedback or an answer instead."""
    say = (text or "").strip()
    if not say:
        raise ValueError("A follow-up must be nonempty")
    if state.get("status") != "TASK_COMPLETE":
        raise ValueError(f"--follow-up continues a finished run; this one is {state.get('status')}. "
                         "Answer its question, send --feedback, or resume it instead")
    # The stage the run's first request went to after recognition. A follow-up that skipped
    # requirements gathering changed the saved one, so it is kept from the first turn.
    first = (state.get("turns") or [{}])[0].get("previous", {}).get("first_stage")
    first_stage = first or (state.get("workflow") or {}).get("then") or workflows.planner_stage(state)
    previous = {"task": state.get("task", ""), "workflow": workflows.kind(state), "status": state["status"],
                "completed_at": state.get("completed_at"), "first_stage": first_stage}
    review = carried_review(state, workspace)
    if review:
        previous["review"] = review
    # A new request can revise the previous contract. Reuse the recorded-feedback
    # identity understood by planning and the contract guard, without granting approval.
    contract = state.get("goal_contract")
    event = {"kind": "brief_feedback", "id": "feedback-" + uuid.uuid4().hex[:12],
             "actor": "user_cli", "at": now, "text": say,
             "contract_token": identity.token(contract) if contract else state.get("requirements_artifact_token", "")}
    state.setdefault("brief_feedback", []).append(event)
    state.setdefault("user_events", []).append(event)
    state.setdefault("turns", []).append({"at": now, "say": say, "previous": previous})
    state["task"] = (f"{say}\n\nThis follows up an earlier request in the same conversation"
                     f" ({previous['workflow'] or 'a finished job'}): {previous['task']}")
    # The findings of a review stand in for requirements gathering when the next job acts on them.
    workflows.begin(state, workflows.planner_stage(state) if review and review["blocking"] else first_stage)
    state.update(status="RUNNING", phase="DISCOVERING")
    for key in ("completed_at", "stop_reason"):
        state.pop(key, None)


def carried_review(state: dict, workspace) -> dict | None:
    """The finished review's findings, from its saved report, or None when the run was no review."""
    review = state.get("review") or {}
    if workflows.kind(state) != "review" or not review.get("report_path"):
        return None
    path = Path(workspace) / review["report_path"]
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"The review's report {review['report_path']} cannot be read: {error}") from None
    findings = [{key: finding.get(key) for key in FINDING_FIELDS}
                for finding in report.get("findings") or [] if isinstance(finding, dict)]
    return {"report_path": review["report_path"], "verdict": review.get("verdict"),
            "change_under_review": review.get("change_under_review", ""),
            "change_patch": review.get("change_patch") or "",
            "blocking": [f for f in findings if f["severity"] == "blocking"],
            "advisory": [f for f in findings if f["severity"] != "blocking"]}


def current(state: dict) -> dict | None:
    """The newest follow-up turn, or None for a run that is still on its first request."""
    turns = state.get("turns") or []
    return turns[-1] if turns else None


def review_findings(state: dict) -> dict | None:
    """The review findings the Planner plans a fix from, or None.

    Present when the newest turn follows up a review with blocking findings and the job
    recognized for it builds or fixes code."""
    turn = current(state)
    review = (turn or {}).get("previous", {}).get("review")
    if not review or not review["blocking"] or workflows.kind(state) not in ("build", "bugfix"):
        return None
    return {key: review[key] for key in ("report_path", "change_under_review", "change_patch",
                                         "blocking", "advisory")}
