"""Ask the person when a planner rewords contract text they approved (#1691).

The revision guard (autocode_contract_revision) refuses a protected rewording without a saved
user answer. Before this, a planner that judged approved wording wrong had its report rejected,
repaired and finally paused as invalid output, and nobody asked the one person who could decide.
Once a plan has been approved, such a refusal now becomes one question: the approved and the
proposed wording side by side, answered through the ordinary `--answer` path. The answer is saved
like any other (`state["answers"]`), so the next planner can cite it as its `user_answer` basis.

Nothing here writes a contract or decides for the person. The question is checked against the
current contract (accepts) before it can be published, so a stale one is never asked.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

PREFIX = "rewording-"
ACCEPT = "Accept the new wording"
KEEP = "Keep the approved wording"


class ProtectedRewording(ValueError):
    """A planner reworded approved text without a saved answer; ``rewordings`` lists each change."""

    def __init__(self, message: str, rewordings: list[dict]):
        super().__init__(message)
        self.rewordings = rewordings


def contract_token(state: RunState) -> str:
    contract = state.get("goal_contract") or {}
    return f"r{contract.get('revision')}:{contract.get('hash')}"


def approved_text(body: dict, item) -> str | None:
    """The approved wording of ``item``: a criterion by ID, or a protected list entry itself."""
    for row in body.get("acceptance_criteria") or []:
        if isinstance(row, dict) and row.get("id") == item:
            return row.get("criterion")
    return item if isinstance(item, str) else None


def question(state: RunState, rewordings: list[dict]) -> dict:
    """The person's question. A row names a criterion's ``approved`` and ``proposed`` text; it may add the proofs
    (``approved_proof``/``proposed_proof``) when those differ, and the blocking review ``concern`` that asked for
    the change in a plan nobody approved (#1630)."""
    reviewed = any(row.get("concern") for row in rewordings)
    lines = [
        "The Plan Reviewer asked the Planner to change text traced to your request:"
        if reviewed
        else "The Planner wants to change wording you approved:"
    ]
    for row in rewordings:
        if row.get("concern"):
            lines.append(f"{row['item']}: asked by the Plan Reviewer's blocking concern {row['concern']}")
        lines += [
            f"{row['item']} approved: {json.dumps(row['approved'])}",
            f"{row['item']} proposed: {json.dumps(row['proposed'])}",
        ]
        if row.get("approved_proof") != row.get("proposed_proof"):
            lines += [
                f"{row['item']} approved proof: {json.dumps(row.get('approved_proof') or '')}",
                f"{row['item']} proposed proof: {json.dumps(row.get('proposed_proof') or '')}",
            ]
    lines.append(f"Reply '{ACCEPT}' to let the next plan use it, or '{KEEP}'.")
    return {
        "id": PREFIX + util.digest({"token": contract_token(state), "rewordings": rewordings})[:12],
        "question": "\n".join(lines),
        "why": "Text traced to your request changes only with your answer."
        if reviewed
        else "A plan you approved changes only with your answer.",
        "options": [ACCEPT, KEEP],
        "proposed_default": KEEP,
    }


def accepts(state: RunState, proposal: dict) -> bool:
    """True for exactly the question this module would ask about the current contract."""
    evidence = (proposal.get("evidence") or {}).get("rewording")
    questions = proposal.get("questions") or []
    if proposal.get("scope") != "clarification" or not isinstance(evidence, dict) or len(questions) != 1:
        return False
    rewordings = evidence.get("rewordings") or []
    body = (state.get("goal_contract") or {}).get("body") or {}
    return (
        evidence.get("contract_token") == contract_token(state)
        and bool(rewordings)
        and all(approved_text(body, row.get("item")) == row.get("approved") for row in rewordings)
        and questions[0] == question(state, rewordings)
    )


def ask(state: RunState, record: dict, error, queue) -> bool:
    """Queue the person's question for a refused rewording of approved text; False for any other error.

    ``queue`` is autocode_resolver_human.queue; the write that follows publishes the question.
    """
    if not isinstance(error, ProtectedRewording) or not state.get("goal_contract"):
        return False
    state.pop("pending_report_repair", None)  # the person's answer, not a repair, settles this report
    queue(
        state,
        "clarification",
        {"stage": record.get("original_stage") or record["stage"], "output": record.get("output")},
        questions=[question(state, error.rewordings)],
        evidence={"rewording": {"contract_token": contract_token(state), "rewordings": error.rewordings}},
        phase="DISCOVERING",
        next_stage=state.get("next_stage"),
    )
    return True
