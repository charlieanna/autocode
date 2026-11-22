"""The stable runner context compared with a recorded provider dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def launch(state: RunState, *, stage, iteration, report_only, original_stage, changed_code_proof):
    return {
        "stage": stage,
        "iteration": iteration,
        "turn": len(state.get("turns") or []) + 1,
        "milestone_id": (state.get("current_task") or {}).get("milestone_id"),
        "task_id": (state.get("current_task") or {}).get("id"),
        "contract_hash": (state.get("goal_contract") or {}).get("hash"),
        "criteria_revision": state.get("criteria_revision"),
        "report_only": report_only,
        "original_stage": original_stage if report_only else None,
        "consumed": sum(isinstance(row.get("replay"), dict) for row in state.get("stages", [])),
        "changed_code_proof": changed_code_proof,
        "regression_proof_path": (state.get("regression_proof") or {}).get("path"),
    }
