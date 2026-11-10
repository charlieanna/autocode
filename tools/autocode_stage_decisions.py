"""Which stages a run ran and which it skipped, with the reason, in one record (``stage_decisions``).

The runner already decides before each stage whether it runs: adaptive planning approves a draft its first
review does not block, so the Planner's revision and the final review do not run; Requirements answers a
discussion itself, so nothing is planned or built; a saved compact bug-fix run left the Planner's draft out
(autocode_stage_policy). Each rule keeps its own record, so no one place said why a stage ran or did not.
``view`` reads those records and the saved stage launches and lists, for the status view:

- one ``run`` row for every stage the run launched, in the order it first launched, with how many times it
  launched (report repairs are not launches of their own) and the latest launch's ``effort_choice``, the
  reasoning level rule that applied to it (autocode_review_effort.launch), or None;
- one ``skip`` row for every stage a saved decision passed over and that never launched, with the reason in
  words and what covers its job (``covered_by``, in autocode_stage_policy's terms), placed after the stage
  that decided it.

A copy of saved decisions, never a recomputation and never a new one: it changes no stage, grants nothing, and
a decision the run did not save is not shown. Whole stages are not reused today, so no row says ``reuse``;
reused evidence inside a stage (a regression baseline, a report repair's execution) stays in that stage's own
record. Pure over the state dict; imports only the role names and the stage-policy record.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
    from . import autocode_stage_policy as stage_policy
except ImportError:
    import autocode_roles as roles
    import autocode_stage_policy as stage_policy

RUN, SKIP = "run", "skip"
APPROVAL = "person:approve_goal"
# The stages a discussion Requirements answered itself never reaches (autocode_requirements_chat.answer_now).
NOT_PLANNED = ("draft_plan", "review_plan", "builder", "tester", "review_completion")
ANSWERED_BY = "requirements_gather"  # autocode_requirements_answer.STAGE


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _launches(state: RunState) -> list[dict]:
    """Saved launches, then the running one; report repairs and runner-only steps are left out."""
    stages = state.get("stages")
    records = [*(stages if isinstance(stages, list) else []), state.get("active_stage")]
    return [
        record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("stage"), str)
        and not record["stage"].endswith("_report_repair")
        and not record.get("report_only")
        and roles.job_for_stage(record["stage"], state) is not None
    ]


def _row(state: RunState, stage: str, decision: str, *, reason=None, covered_by=()):
    return {
        "stage": stage,
        "job": roles.job_for_stage(stage, state),
        "label": roles.screen_name(stage, state),
        "decision": decision,
        "reason": reason,
        "covered_by": list(covered_by),
        "launches": 0,
        "effort_choice": None,
    }


def _skips(state: RunState) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """``(after, stage, reason, covered_by)`` for every stage a saved decision passed over."""
    found: list[tuple[str, str, str, tuple[str, ...]]] = []
    adaptive = _mapping(_mapping(state.get("planning")).get("adaptive"))
    approved = adaptive.get("approved_at")
    if isinstance(approved, str) and approved.startswith("review_plan#"):
        number = approved.removeprefix("review_plan#")
        covered: tuple[str, ...] = ("review_plan", APPROVAL)
        found.append(
            ("review_plan", "revise_plan", "no revision: the Plan Reviewer raised no blocking concern", covered)
        )
        found.append(
            (
                "revise_plan",
                "finalize_plan",
                f"no final review: the Plan Reviewer approved the plan at its review {number}",
                covered,
            )
        )
    answer = _mapping(state.get("answer"))
    if state.get("status") == "TASK_COMPLETE" and answer.get("answered_by") == ANSWERED_BY:
        reason = "Requirements answered the question from the code, and nothing was to be written"
        found.extend((ANSWERED_BY, stage, reason, (ANSWERED_BY,)) for stage in NOT_PLANNED)
    for decision in (stage_policy.view(state) or {}).get("decisions") or []:
        if isinstance(decision, dict) and decision.get("decision") == "omitted" and decision.get("stage"):
            kept = tuple(str(item) for item in decision.get("covered_by") or [])
            found.append(("investigate_bug", str(decision["stage"]), str(decision.get("reason") or ""), kept))
    return found


def view(state: RunState) -> list[dict]:
    """The status view's ``stage_decisions``: a ``run`` row per launched stage, a ``skip`` row per passed-over one."""
    rows: list[dict] = []
    for record in _launches(state):
        row = next((row for row in rows if row["stage"] == record["stage"]), None)
        if row is None:
            row = _row(state, record["stage"], RUN)
            rows.append(row)
        row["launches"] += 1
        row["effort_choice"] = (
            copy.deepcopy(record["effort_choice"]) if isinstance(record.get("effort_choice"), dict) else None
        )
    pending: list[tuple[str, dict]] = []
    for after, stage, reason, covered in _skips(state):
        # A stage that launched ran, whatever a decision said earlier; one skip row per stage.
        if not any(row["stage"] == stage for row in rows) and not any(row["stage"] == stage for _, row in pending):
            pending.append((after, _row(state, stage, SKIP, reason=reason, covered_by=covered)))
    shown: list[dict] = []

    def place(row: dict) -> None:
        shown.append(row)  # then each skip the stage decided, in the order decided, and what those decided
        for item in [item for item in pending if item[0] == row["stage"]]:
            pending.remove(item)
            place(item[1])

    for row in rows:
        place(row)
    shown.extend(row for _, row in pending)  # decided by a stage the run has no launch of
    return shown
