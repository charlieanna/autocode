"""Bind recovery advice to the inputs it diagnosed, without granting retries."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_roles as roles
    import autocode_util as util


def binding(state: RunState, stage: str) -> dict:
    if stage not in roles.ALL_PLANNING_STAGES:
        contract = state.get("goal_contract") or {}
        # Core recomputes this at interception, including replay. Model reports neither
        # supply nor cite it. A saved snapshot may be an inline record or an artifact path.
        return {
            "execution_context": util.digest(
                {
                    "task": state.get("task"),
                    "current_task": state.get("current_task"),
                    "contract": {key: contract.get(key) for key in ("revision", "hash")},
                    "source_snapshot": state.get("source_snapshot"),
                    "answers": state.get("answers") or {},
                    "feedback": state.get("brief_feedback") or [],
                }
            )
        }
    contract = state.get("goal_contract") or {}
    planning = state.get("planning") or {}
    return {
        "planning_context": util.digest(
            {
                "task": state.get("task"),
                "contract": {key: contract.get(key) for key in ("revision", "hash", "body")},
                "current_task": state.get("current_task"),
                "answers": state.get("answers") or {},
                "feedback": state.get("brief_feedback") or [],
                "turn": planning.get("turn_event_id"),
                "reports": {
                    key: row.get("report")
                    for key, row in (planning.get("reports") or {}).items()
                    if isinstance(row, dict)
                },
            }
        )
    }


def applies(state: RunState, row: dict, stage: str) -> bool:
    """Unbound legacy advice remains history, never a current instruction."""
    expected = binding(state, stage)
    return all(row.get(key) == value for key, value in expected.items())
