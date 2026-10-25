"""The stage-policy record: which planning path a run took and why (#2021, the #1895 contract's first field).

autocode_compact_planning decides whether a bug-fix run plans on the compact path (the runner's draft, one
Plan Reviewer, the person's approval) or the full one (the Planner's paid draft first). It writes the
decision here, once per job, under ``state["workflow"]["stage_policy"]``; the status view shows a copy as
``stage_policy`` (view). The record names the path, every signal that kept the full path, the hashes the
decision rests on, the one stage decision it makes (``astra_discovery`` omitted or included, with what
covers an omission) and, after a review that could not approve the compact draft, the fallback. A client
renders it and nothing more: the record grants no authority, and a run that never decided shows null.

Bottom layer: pure over the state dict; imports nothing from AutoCode.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

RECORD = "stage_policy"  # under state["workflow"], and the status view's field
VERSION = 1
PLANNER_DRAFT = "astra_discovery"
# What covers the Planner's omitted draft on the compact path (docs/plans/economical-execution/verification.md):
# the runner writes the draft from bound records, the Plan Reviewer reviews it, the person approves the
# displayed token, and the runner proves each regression case red on the original and green after the fix.
COVERED_BY = ("runner:compact_draft", "astra_challenge", "person:approve_goal", "runner:regression_proof")


def record(state: RunState, *, path: str, reasons, basis: dict, decision: str, reason: str) -> dict:
    """Save the decision for this job; the only writer of the record besides fall_back."""
    entry = {
        "version": VERSION,
        "path": path,
        "reasons": list(reasons),
        "basis": dict(basis),
        "decisions": [
            {
                "stage": PLANNER_DRAFT,
                "decision": decision,
                "reason": reason,
                "covered_by": list(COVERED_BY) if decision == "omitted" else [],
            }
        ],
        "fallback": None,
    }
    workflow = state.get("workflow")
    if not isinstance(workflow, dict):
        workflow = state["workflow"] = {}
    workflow[RECORD] = entry
    return entry


def fall_back(state: RunState, *, stage: str, reason: str, **facts) -> dict | None:
    """Mark the compact path abandoned at ``stage`` for ``reason`` (plus any ``facts`` worth showing, such as a
    refunded review call): the Planner's draft is included after all."""
    entry = current(state)
    if entry is None:
        return None
    entry.update(path="full", fallback={"from": stage, "reason": reason, **facts})
    for decision in entry.get("decisions") or []:
        if decision.get("stage") == PLANNER_DRAFT:
            decision.update(decision="included", reason=reason, covered_by=[])
    return entry


def current(state: RunState) -> dict | None:
    """The saved record, or None when this run never decided (older runs, jobs other than a bug fix)."""
    workflow = state.get("workflow")
    entry = workflow.get(RECORD) if isinstance(workflow, dict) else None
    return entry if isinstance(entry, dict) else None


def view(state: RunState) -> dict | None:
    """The status view's ``stage_policy``: a copy of the saved record, never a recomputation."""
    entry = current(state)
    return copy.deepcopy(entry) if entry is not None else None
