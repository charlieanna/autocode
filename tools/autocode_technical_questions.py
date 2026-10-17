"""Keep internal engineering choices out of the human clarification queue.

These receipts belong to AutoCode, not the user. They supply planning context,
never authority to change protected behavior, permissions or approved plans.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def split(questions, obligations):
    """Select explicit technical defaults; retain intent and rejected assumptions."""
    internal, human = [], []
    for question in questions:
        selected = (
            question.get("category") == "technical"
            and question.get("kind", "decision") in ("decision", "inferable")
            and question.get("proposed_default", "").strip()
            and question["id"] not in obligations
        )
        (internal if selected else human).append(question)
    return internal, human


def settle(state: RunState, questions, contract_token):
    """Record AutoCode's defaults and return the questions the human must answer."""
    if not state.get("settings", {}).get("joint_planning"):
        return questions
    obligations = {row["id"] for row in state.get("deferred_obligations", []) if row.get("status") != "resolved"}
    obligations.update((state.get("clarification_episode") or {}).get("converted_to_decision", []))
    internal, human = split(questions, obligations)
    settled = []
    for event in reversed(state.get("user_events", [])):
        if event.get("actor") == "user_cli" and event.get("kind") in ("answer", "brief_feedback", "goal_edit"):
            break
        if event.get("kind") == "technical_decision" and event.get("actor") == "autocode":
            settled.append(event.get("question"))
    for question in internal:
        if question in settled:
            raise ValueError(
                f"Internal technical question {question['id']} is already settled; use the saved technical_decision "
                "event and return a repaired draft, not another question"
            )
    for question in internal:
        event = {
            "kind": "technical_decision",
            "actor": "autocode",
            "at": util.now(),
            "question_id": question["id"],
            "question": copy.deepcopy(question),
            "text": question["proposed_default"],
            "contract_token": contract_token,
        }
        state.setdefault("user_events", []).append(event)
    return human
