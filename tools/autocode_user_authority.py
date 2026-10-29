"""Whether a declared contract change cites authority the person actually gave.

The revision guard and the workspace-scope gate must share one rule for a saved
basis (#2467), so it lives here alone: pure functions over run state, importing
nothing from AutoCode. A saved answer, a delegated answer or an authenticated
feedback event is the person's own authority; ``agent_proposed`` and empty or
unknown references never are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def saved_user_basis(state: RunState, basis, answer_id):
    if basis == "user_answer":
        return bool(answer_id) and answer_id in state.get("answers", {})
    if basis == "delegated":
        answer = state.get("answers", {}).get(answer_id) if answer_id else None
        return isinstance(answer, dict) and answer.get("kind") == "delegated"
    if basis == "user_feedback":
        return bool(answer_id) and any(
            event.get("id") == answer_id and event in state.get("user_events", [])
            for event in state.get("brief_feedback", [])
        )
    return False
