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
                                        "summary", "satisfied", "concerns": [...], "questions": [...],
                                        "blocking": [...], "advisory": [...], "revision", "revisions": [...]}
                                    or absent}}]
        One entry per follow-up. Read by autocode_workflows.follow_up (the recognizer judges the
        newest message; it also gets previous.design, so "Build it." after a design turn names that
        design), by design_review_to_revise (a reply to a design review: the Architect revises the
        report carried here, autocode_design_job), by review_findings (the Planner's handoff) and by
        the status and progress views. A review's blocking and advisory are its OPEN concerns
        (id, area, summary); concerns is every concern as the report has it, resolved ones included.
        stage_index is len(state["stages"]) when the turn was said: the turn's stage records start
        there, and turn_changes reads it when the NEXT follow-up is said. event_id is the turn's
        brief_feedback receipt. previous.wrote is what the finished job left in the workspace: its
        report or note, then every file that differs from the turn's start snapshot; the
        rewritten task names it.
        A turn that builds the design the previous turn proposed gains "fresh_plan": {"archived":
        [state key], "contract": the archived contract's token or None, "findings": [ledger row]},
        written once by plan_afresh when the design check passes: the keys it moved to their
        histories, the contract the build no longer revises, and the open non-blocking findings it
        took out of findings_ledger. Read by autocode_findings.allocate_id (an archived finding's id
        is never given again) and by people reading the run state.
    task: rewritten to the follow-up, followed by the earlier request (and what it wrote) as
        context, so every later stage reads what the user now wants. The original request stays
        in turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .autocode_run_state import RunState

_PlannedStateKey = Literal["goal_contract", "requirements_handoff", "planning", "current_task"]
_PlannedHistoryKey = Literal["contract_history", "requirements_history", "planning_history", "task_archive"]

try:
    from . import autocode_source_scope as source_scope
except ImportError:
    import autocode_source_scope as source_scope


import hashlib
import json
import subprocess
import uuid
from pathlib import Path

try:
    from . import autocode_contract_identity as identity
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_contract_identity as identity
    import autocode_util as util
    import autocode_workflows as workflows

FINDING_FIELDS = ("id", "severity", "file", "lines", "summary", "evidence")
CONCERN_FIELDS = ("id", "area", "summary")
# A design review's concern as the Architect revises it: everything the report says about it.
REVISED_FIELDS = ("id", "area", "severity", "status", "resolution", "summary", "evidence", "example", "probe")
# Where each read-only job leaves its report or note: (state key, field).
REPORTS = {
    "review": ("review", "report_path"),
    "design": ("design_review", "report_path"),
    "discuss": ("answer", "note_path"),
}
NAMED = 8  # at most this many written paths are named in the rewritten task
# --answer and --delegate answer a question the run is waiting on; a finished run waits on none.
ANSWER_FINISHED = (
    "This run is finished and waits for no answer; reply to the questions in its report with --follow-up TEXT"
)
# Actions on a run that is still working, by their argparse names. On a finished run each would
# reopen it without a new turn (--feedback restarts discovery, --edit-goal installs a draft).
ANSWERS = (("answer", "--answer"), ("delegate", "--delegate"), ("delegate_all", "--delegate-all"))
REOPENING = (("reject_assumption", "--reject-assumption"), ("feedback", "--feedback"), ("edit_goal", "--edit-goal"))


def accept(state: RunState, text: str, workspace, now: str) -> None:
    """Record ``text`` as the next turn and reopen the finished run to recognize it.

    Raises ValueError, leaving ``state`` unchanged, when the run is not finished or the
    message is empty: a running or paused run takes --feedback or an answer instead."""
    say = (text or "").strip()
    if not say:
        raise ValueError("A follow-up must be nonempty")
    if state.get("status") != "TASK_COMPLETE":
        raise ValueError(
            f"--follow-up continues a finished run; this one is {state.get('status')}. "
            "Answer its question (--answer or --delegate), approve or correct it "
            "(--approve-goal or --feedback), or resume it (--resume-paused) instead"
        )
    # The stage the run's first request went to after recognition. A follow-up that skipped
    # requirements gathering changed the saved one, so it is kept from the first turn.
    first = (state.get("turns") or [{}])[0].get("previous", {}).get("first_stage")
    first_stage = first or (state.get("workflow") or {}).get("then") or workflows.planner_stage(state)
    changes = turn_changes(state, workspace)
    wrote_paths = wrote(state, changes)
    previous = {
        "task": state.get("task", ""),
        "workflow": workflows.kind(state),
        "status": state["status"],
        "completed_at": state.get("completed_at"),
        "first_stage": first_stage,
        "wrote": wrote_paths,
    }
    review = carried_review(state, workspace)
    if review:
        previous["review"] = review
    design = carried_design(state, workspace, changes)
    if design:
        previous["design"] = design
    # A new request can revise the previous contract. Reuse the recorded-feedback
    # identity understood by planning and the contract guard, without granting approval.
    contract = state.get("goal_contract")
    event = {
        "kind": "brief_feedback",
        "id": "feedback-" + uuid.uuid4().hex[:12],
        "actor": "user_cli",
        "at": now,
        "text": say,
        "contract_token": identity.token(contract) if contract else state.get("requirements_artifact_token", ""),
    }
    state.setdefault("brief_feedback", []).append(event)
    state.setdefault("user_events", []).append(event)
    state.setdefault("turns", []).append(
        {
            "at": now,
            "say": say,
            "stage_index": len(state.get("stages") or []),
            "event_id": event["id"],
            "previous": previous,
        }
    )
    state["task"] = (
        f"{say}\n\nThis follows up an earlier request in the same conversation"
        f" ({previous['workflow'] or 'a finished job'}{_named(wrote_paths)}): {previous['task']}"
    )
    # The findings of a review stand in for requirements gathering when the next job acts on them.
    workflows.begin(state, workflows.planner_stage(state) if review and review["blocking"] else first_stage)
    state.update({"status": "RUNNING", "phase": "DISCOVERING"})
    state.pop("completed_at", None)
    state.pop("stop_reason", None)


def refuse_on_finished(args) -> None:
    """Raise ValueError when ``args`` (the CLI's) carry an action that only a run still working
    takes: a finished run is continued with --follow-up, which records the new turn."""
    given = lambda name: getattr(args, name, None) not in (None, False, [])
    if any(given(name) for name, _ in ANSWERS) and not any(given(name) for name, _ in REOPENING):
        raise ValueError(ANSWER_FINISHED)
    flags = [flag for name, flag in ANSWERS + REOPENING if given(name)]
    if flags:
        raise ValueError(
            f"This run is finished; {', '.join(flags)} would reopen it without a new turn. "
            "Say the next thing with --follow-up TEXT"
        )


def turn_changes(state: RunState, workspace) -> list[str]:
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
    first = next(
        (
            record["before_ref"]
            for record in (state.get("stages") or [])[start:]
            if isinstance(record, dict) and record.get("before_ref")
        ),
        None,
    )
    try:
        before = json.loads(Path(first).read_text()) if first else None
        changed = (
            util.changed_paths(before, source_scope.snapshot(workspace, state)) if isinstance(before, dict) else []
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        return []
    return [path for path in changed if not path.startswith(".autocode/")]


def wrote(state: RunState, changes: list[str]) -> list[str]:
    """What the finished job left in the workspace: its report or note (the runner writes those
    after the stage), then the other files its turn changed (``turn_changes``)."""
    entry = REPORTS.get(workflows.kind(state) or "")
    if entry:
        key, field = entry
        block = state.get(key)
        report = str(block.get(field) or "") if isinstance(block, dict) else ""
    else:
        report = ""
    return list(dict.fromkeys([*([report] if report else []), *changes]))


def _named(paths: list[str]) -> str:
    if not paths:
        return ""
    more = len(paths) - NAMED
    return "; it wrote " + ", ".join(paths[:NAMED]) + (f" and {more} more" if more > 0 else "")


def carried_design(state: RunState, workspace, changes: list[str] | None = None) -> dict | None:
    """What a finished design job produced, or None when the run was no design job.

    A new design (propose mode) is the Markdown documents its turn wrote, README.md files left
    out (a design folder's index is no design), and only regular files inside the workspace (a
    symbolic link could point anywhere); a design review is its whole saved report, which a reply
    makes the Architect revise. A report that cannot be read, or that changed since the Architect's
    review wrote it (its saved sha256), raises ValueError: the revision would start from text the
    Architect never wrote."""
    found = state.get("design_review") or {}
    if workflows.kind(state) != "design" or found.get("mode") not in ("propose", "review"):
        return None
    if found["mode"] == "propose":
        return {
            "mode": "propose",
            "documents": [
                path
                for path in (turn_changes(state, workspace) if changes is None else changes)
                if path.lower().endswith(".md")
                and Path(path).name.lower() != "readme.md"
                and workflows.workspace_file(workspace, path)
            ],
        }
    if not found.get("report_path"):
        return None
    try:
        raw = (Path(workspace) / found["report_path"]).read_bytes()
        report = json.loads(raw)
        if not isinstance(report, dict):
            raise ValueError("it is not a JSON object")
    except (OSError, ValueError) as error:
        raise ValueError(f"The design review's report {found['report_path']} cannot be read: {error}") from None
    if found.get("report_sha256") and hashlib.sha256(raw).hexdigest() != found["report_sha256"]:
        raise ValueError(f"{found['report_path']} changed since the Architect's review; restore it or start a new run")
    concerns = [
        {key: c.get(key, "open" if key == "status" else "") for key in REVISED_FIELDS}
        for c in report.get("concerns") or []
        if isinstance(c, dict)
    ]
    unresolved = [c for c in concerns if c["status"] != "resolved"]
    revisions = [row for row in report.get("revisions") or [] if isinstance(row, dict)]
    return {
        "mode": "review",
        "report_path": found["report_path"],
        "design_under_review": report.get("design_under_review", found.get("design_under_review", "")),
        "verdict": report.get("verdict"),
        "summary": report.get("summary", ""),
        "satisfied": [str(goal) for goal in report.get("satisfied") or []],
        "concerns": concerns,
        "blocking": [{key: c[key] for key in CONCERN_FIELDS} for c in unresolved if c["severity"] == "blocking"],
        "advisory": [{key: c[key] for key in CONCERN_FIELDS} for c in unresolved if c["severity"] != "blocking"],
        "questions": [
            {"id": q.get("id"), "question": q.get("question"), "options": list(q.get("options") or [])}
            for q in report.get("questions") or []
            if isinstance(q, dict)
        ],
        # A report written before revisions existed is its own first revision.
        "revision": report["revision"] if isinstance(report.get("revision"), int) else 1,
        "revisions": revisions,
    }


def carried_review(state: RunState, workspace) -> dict | None:
    """The finished review's findings, from its saved report, or None when the run was no review."""
    review = state.get("review") or {}
    if workflows.kind(state) != "review" or not review.get("report_path"):
        return None
    path = Path(workspace) / review["report_path"]
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"The review's report {review['report_path']} cannot be read: {error}") from None
    findings = [
        {key: finding.get(key) for key in FINDING_FIELDS}
        for finding in report.get("findings") or []
        if isinstance(finding, dict)
    ]
    return {
        "report_path": review["report_path"],
        "verdict": review.get("verdict"),
        "change_under_review": review.get("change_under_review", ""),
        "change_patch": review.get("change_patch") or "",
        "blocking": [f for f in findings if f["severity"] == "blocking"],
        "advisory": [f for f in findings if f["severity"] != "blocking"],
    }


def current(state: RunState) -> dict | None:
    """The newest follow-up turn, or None for a run that is still on its first request."""
    turns = state.get("turns") or []
    return turns[-1] if turns else None


def design_review_to_revise(state: RunState) -> dict | None:
    """The design review the newest turn replies to, or None.

    Present when the newest turn follows a design review and was recognized as design: the
    Architect then revises that review (autocode_design_job). It is the carried report plus the
    user's message (``said``) and the turn's receipt (``event_id``)."""
    turn = current(state)
    design = (turn or {}).get("previous", {}).get("design") or {}
    if design.get("mode") != "review" or workflows.kind(state) != "design":
        return None
    assert turn is not None
    return {**design, "said": turn["say"], "event_id": turn.get("event_id")}


# What a build of an earlier turn's design does not inherit: (state key, the existing list it moves to).
PLANNED_FOR_THE_EARLIER_JOB: tuple[tuple[_PlannedStateKey, _PlannedHistoryKey], ...] = (
    ("goal_contract", "contract_history"),
    ("requirements_handoff", "requirements_history"),
    ("planning", "planning_history"),
    ("current_task", "task_archive"),
)


def plan_afresh(state: RunState) -> None:
    """Plan a follow-up that builds the design the previous turn proposed from that design.

    Called by autocode_design_check_job when its check passes. Only when the previous turn was a
    design job that proposed the design being built (``previous.design.mode`` is propose): its
    contract, requirements handoff, planning record and task were made for that job, which delivers
    a document and writes no code. The build does not revise them. They move to their existing
    histories (PLANNED_FOR_THE_EARLIER_JOB), and the Planner drafts a new contract from the approved
    design and the user's message, which the user approves as usual, as for a first request to build
    an approved design. Whatever earlier turns agreed and the design turn's contract carried goes with
    it: the design is now the requirements. A build after a design REVIEW revises the contract in
    force as before, since a review installs none of its own.

    The criteria the archived contract defined go with it: the legacy acceptance_criteria checklist,
    its criteria_revision and the last validation (to validation_archive) are reset as on a new run.
    Open non-blocking findings cite those criteria, whose IDs the new contract reuses for other
    things: they move out of findings_ledger into the turn's ``fresh_plan``, unresolved, and out of
    unresolved_findings. A finished turn has no open blocking finding; one that is somehow left stays
    in the ledger and still blocks. Runs once per turn; a first request has no earlier turn."""
    turn = current(state)
    if not turn or "fresh_plan" in turn or ((turn.get("previous") or {}).get("design") or {}).get("mode") != "propose":
        return
    contract = identity.token(state["goal_contract"]) if state.get("goal_contract") else None
    archived = []
    for key, history in PLANNED_FOR_THE_EARLIER_JOB:
        if state.get(key):
            state.setdefault(history, []).append(state.pop(key))
            archived.append(key)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append(
            {
                "reason": "A follow-up builds the design the previous turn proposed, from a new plan",
                "validation": state.pop("validation"),
            }
        )
    state.update({"acceptance_criteria": [], "criteria_revision": None})
    ledger = state.get("findings_ledger") or []
    findings = [row for row in ledger if row.get("status") == "open" and row.get("blocking") is False]
    if findings:
        state["findings_ledger"] = [row for row in ledger if not any(row is moved for moved in findings)]
    still_open = {row.get("id") for row in state.get("findings_ledger") or [] if row.get("status") == "open"}
    state["unresolved_findings"] = [
        row for row in state.get("unresolved_findings") or [] if isinstance(row, dict) and row.get("id") in still_open
    ]
    turn["fresh_plan"] = {"archived": archived, "contract": contract, "findings": findings}


def review_findings(state: RunState) -> dict | None:
    """The review findings the Planner plans a fix from, or None.

    Present when the newest turn follows up a review with blocking findings and the job
    recognized for it builds or fixes code."""
    turn = current(state)
    review = (turn or {}).get("previous", {}).get("review")
    if not review or not review["blocking"] or workflows.kind(state) not in ("build", "bugfix"):
        return None
    return {key: review[key] for key in ("report_path", "change_under_review", "change_patch", "blocking", "advisory")}
