"""A follow-up message to a finished run: the next turn of the same conversation (issue #51).

``autocode --run-dir R --follow-up TEXT`` on a finished run records TEXT as a new turn and
reopens the run: the job recognizer reads TEXT (with the earlier turn as context) and picks
the next workflow, in the same run directory. "Review PR #184" then "Fix them." is one
conversation: the review's findings carry forward, and a build that acts on them is planned
from them, the way a bug fix is planned from its diagnosis (autocode_bug_job), so
requirements gathering is skipped. Plan review and the user's approval still apply.

This module reads the run state, the saved reports and snapshots, and the workspace (a turn's
changes are its start snapshot against the workspace now). It imports nothing from the runner.

State keys written here:
    brief_feedback, user_events: the same user-input receipt in both existing ledgers.
        Planning receives its exact text and ID through the standard handoff; the
        contract guard accepts that ID as user_feedback for declared revisions.
        Recording a follow-up does not grant plan approval or recovery allowance.
    turns: [{"at", "say", "stage_index", "event_id",
             "previous": {"task", "workflow", "status", "completed_at", "first_stage", "wrote": [path],
                          "review": {"report_path", "change_under_review", "change_patch", "verdict",
                                     "blocking": [...], "advisory": [...]} or absent,
                          "design": {"mode": "propose", "documents": [path]}
                                    or {"mode": "review", "report_path", "design_under_review", "verdict",
                                        "blocking": [...], "advisory": [...], "questions": [...]} or absent}}]
        One entry per follow-up. Read by autocode_workflows.follow_up (the recognizer judges the
        newest message; it also gets previous.design, so "Build it." after a design turn names that
        design), by review_findings (the Planner's handoff) and by the status and progress views.
        stage_index is len(state["stages"]) when the turn was said: the turn's stage records start
        there, and turn_changes reads it when the NEXT follow-up is said. event_id is the turn's
        brief_feedback receipt. previous.wrote is what the finished job left in the workspace: its
        report or note, then every file that differs from the turn's start snapshot; the
        rewritten task names it.
    task: rewritten to the follow-up, followed by the earlier request (and what it wrote) as
        context, so every later stage reads what the user now wants. The original request stays
        in turns.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

try:
    from . import autocode_workflows as workflows, autocode_contract_identity as identity
    from . import autocode_util as util
except ImportError:
    import autocode_workflows as workflows
    import autocode_contract_identity as identity
    import autocode_util as util

FINDING_FIELDS = ("id", "severity", "file", "lines", "summary", "evidence")
CONCERN_FIELDS = ("id", "area", "summary")
# Where each read-only job leaves its report or note: (state key, field).
REPORTS = {"review": ("review", "report_path"), "design": ("design_review", "report_path"),
           "discuss": ("answer", "note_path")}
NAMED = 8  # at most this many written paths are named in the rewritten task
# --answer and --delegate answer a question the run is waiting on; a finished run waits on none.
ANSWER_FINISHED = ("This run is finished and waits for no answer; reply to the questions in its report "
                   "with --follow-up TEXT")


def accept(state: dict, text: str, workspace, now: str) -> None:
    """Record ``text`` as the next turn and reopen the finished run to recognize it.

    Raises ValueError, leaving ``state`` unchanged, when the run is not finished or the
    message is empty: a running or paused run takes --feedback or an answer instead."""
    say = (text or "").strip()
    if not say:
        raise ValueError("A follow-up must be nonempty")
    if state.get("status") != "TASK_COMPLETE":
        raise ValueError(f"--follow-up continues a finished run; this one is {state.get('status')}. "
                         "Answer its question (--answer or --delegate), approve or correct it "
                         "(--approve-goal or --feedback), or resume it (--resume-paused) instead")
    # The stage the run's first request went to after recognition. A follow-up that skipped
    # requirements gathering changed the saved one, so it is kept from the first turn.
    first = (state.get("turns") or [{}])[0].get("previous", {}).get("first_stage")
    first_stage = first or (state.get("workflow") or {}).get("then") or workflows.planner_stage(state)
    changes = turn_changes(state, workspace)
    previous = {"task": state.get("task", ""), "workflow": workflows.kind(state), "status": state["status"],
                "completed_at": state.get("completed_at"), "first_stage": first_stage,
                "wrote": wrote(state, changes)}
    review = carried_review(state, workspace)
    if review:
        previous["review"] = review
    design = carried_design(state, workspace, changes)
    if design:
        previous["design"] = design
    # A new request can revise the previous contract. Reuse the recorded-feedback
    # identity understood by planning and the contract guard, without granting approval.
    contract = state.get("goal_contract")
    event = {"kind": "brief_feedback", "id": "feedback-" + uuid.uuid4().hex[:12],
             "actor": "user_cli", "at": now, "text": say,
             "contract_token": identity.token(contract) if contract else state.get("requirements_artifact_token", "")}
    state.setdefault("brief_feedback", []).append(event)
    state.setdefault("user_events", []).append(event)
    state.setdefault("turns", []).append({"at": now, "say": say, "stage_index": len(state.get("stages") or []),
                                          "event_id": event["id"], "previous": previous})
    state["task"] = (f"{say}\n\nThis follows up an earlier request in the same conversation"
                     f" ({previous['workflow'] or 'a finished job'}{_named(previous['wrote'])}): {previous['task']}")
    # The findings of a review stand in for requirements gathering when the next job acts on them.
    workflows.begin(state, workflows.planner_stage(state) if review and review["blocking"] else first_stage)
    state.update(status="RUNNING", phase="DISCOVERING")
    for key in ("completed_at", "stop_reason"):
        state.pop(key, None)


def turn_changes(state: dict, workspace) -> list[str]:
    """The files the turn now finishing changed: its start snapshot against the workspace now.

    The start is the before-snapshot of the turn's first stage record (from its ``stage_index``;
    0 for the first request). Comparing the ends, not adding up each stage's changed_files,
    counts an attempt the runner set aside by what it left: an abandoned or rejected attempt's
    edits stay in the workspace, while stray writes the runner put back are gone. A turn saved
    before stage_index existed, or one whose snapshot cannot be read, attributes nothing."""
    turns = state.get("turns") or []
    start = turns[-1].get("stage_index") if turns else 0
    if not isinstance(start, int):
        return []
    first = next((record["before_ref"] for record in (state.get("stages") or [])[start:]
                  if isinstance(record, dict) and record.get("before_ref")), None)
    try:
        before = json.loads(Path(first).read_text()) if first else None
        changed = util.changed_paths(before, util.snapshot(workspace)) if isinstance(before, dict) else []
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        return []
    return [path for path in changed if not path.startswith(".autocode/")]


def wrote(state: dict, changes: list[str]) -> list[str]:
    """What the finished job left in the workspace: its report or note (the runner writes those
    after the stage), then the other files its turn changed (``turn_changes``)."""
    key, field = REPORTS.get(workflows.kind(state), (None, None))
    report = str((state.get(key) or {}).get(field) or "") if key else ""
    return list(dict.fromkeys([*([report] if report else []), *changes]))


def _named(paths: list[str]) -> str:
    if not paths:
        return ""
    more = len(paths) - NAMED
    return "; it wrote " + ", ".join(paths[:NAMED]) + (f" and {more} more" if more > 0 else "")


def carried_design(state: dict, workspace, changes: list[str] | None = None) -> dict | None:
    """What a finished design job produced, or None when the run was no design job.

    A new design (propose mode) is the Markdown documents its turn wrote, README.md files left
    out (a design folder's index is no design), and only regular files inside the workspace (a
    symbolic link could point anywhere); a design review is its saved report's verdict, concerns
    and questions. An unreadable report raises ValueError."""
    found = state.get("design_review") or {}
    if workflows.kind(state) != "design" or found.get("mode") not in ("propose", "review"):
        return None
    if found["mode"] == "propose":
        return {"mode": "propose", "documents": [
            path for path in (turn_changes(state, workspace) if changes is None else changes)
            if path.lower().endswith(".md") and Path(path).name.lower() != "readme.md"
            and workflows.workspace_file(workspace, path)]}
    if not found.get("report_path"):
        return None
    try:
        report = json.loads((Path(workspace) / found["report_path"]).read_text())
        if not isinstance(report, dict):
            raise ValueError("it is not a JSON object")
    except (OSError, ValueError) as error:
        raise ValueError(f"The design review's report {found['report_path']} cannot be read: {error}") from None
    concerns = [concern for concern in report.get("concerns") or [] if isinstance(concern, dict)]
    return {"mode": "review", "report_path": found["report_path"],
            "design_under_review": report.get("design_under_review", found.get("design_under_review", "")),
            "verdict": report.get("verdict"),
            "blocking": [{key: c.get(key) for key in CONCERN_FIELDS} for c in concerns if c.get("severity") == "blocking"],
            "advisory": [{key: c.get(key) for key in CONCERN_FIELDS} for c in concerns if c.get("severity") != "blocking"],
            "questions": [{"id": q.get("id"), "question": q.get("question")}
                          for q in report.get("questions") or [] if isinstance(q, dict)]}


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
