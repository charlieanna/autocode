"""The Plan Reviewer's latest verdict, as the status view shows it (``plan_review``).

The Plan Reviewer's concerns were saved only in the run's planning records, and the status view
showed them only inside a conversation's projection. A client, or the model probe, that wanted to
know whether the reviewer blocked a plan, and on what, had to read the run's private state. This
reads the same accepted reports the conversation projection reads
(``autocode_conversation_projection.records``) and says, for the latest accepted review:

- ``review_plan`` (a first-style review): ``approved`` when no concern blocks, else
  ``changes_requested``, with the blocking concern ids;
- ``finalize_plan`` (the final review): ``approved`` when the review resolved every concern and left
  no blocking question for the person, else ``unresolved``, with the unresolved concern ids.

A review carried over from an earlier plan (a planning restart keeps the last challenge so the next
draft answers it) is shown with ``from_earlier_plan`` true: it judged an earlier draft, not this one.

Pure: a copy of saved records, display only; it grants nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_conversation_projection as conversation_projection
except ImportError:
    import autocode_conversation_projection as conversation_projection

# Latest first: a final review always follows the first-style reviews of its cycle.
FINAL_STAGES = ("finalize_plan", "plan_finalize")
CHALLENGE_STAGES = ("review_plan", "plan_review")
CONCERN_FIELDS = ("id", "concern", "blocking", "requested_change")


def project(state: RunState):
    """The latest accepted Plan Reviewer verdict of the current planning cycle, or None before one."""
    planning = state.get("planning")
    if not isinstance(planning, dict):
        return None
    records = {row["stage"]: row for row in conversation_projection.records(state, planning, current_cycle=True)}
    stage = next((name for name in FINAL_STAGES + CHALLENGE_STAGES if name in records), None)
    if stage is None:
        return None
    record, report = records[stage], records[stage]["report"]
    final = stage in FINAL_STAGES
    if final:
        challenge = next((records[name] for name in CHALLENGE_STAGES if name in records), None)
        concerns = _concerns(challenge["report"] if challenge else {})
        decisions = [row for row in report.get("decisions") or () if isinstance(row, dict)]
        contract = report.get("contract") if isinstance(report.get("contract"), dict) else {}
        questions = contract.get("open_blocking_questions")
        blocking = [row.get("concern_id") for row in decisions if not row.get("resolved")]
        verdict = "unresolved" if blocking or questions else "approved"
    else:
        concerns = _concerns(report)
        blocking = [row["id"] for row in concerns if row["blocking"]]
        verdict = "changes_requested" if blocking else "approved"
    return {
        "stage": stage,
        "role": record["role"],
        "model": record["model"],
        "reasoning_effort": record["reasoning_effort"],
        "verdict": verdict,
        "blocking": blocking,
        "concerns": concerns,
        "from_earlier_plan": _inherited(state, stage, record["output"]),
    }


def _concerns(report) -> list:
    return [
        {
            "id": row.get("id"),
            "concern": row.get("concern"),
            "blocking": bool(row.get("blocking")),
            "requested_change": row.get("requested_change"),
        }
        for row in report.get("concerns") or ()
        if isinstance(row, dict)
    ]


def _inherited(state: RunState, stage, output) -> bool:
    history = [row for row in state.get("planning_history") or () if isinstance(row, dict)]
    if not output or not history:
        return False
    earlier = (history[-1].get("reports") or {}).get(stage)
    return isinstance(earlier, dict) and earlier.get("output") == output
