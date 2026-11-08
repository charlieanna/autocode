"""Identify the one review inspecting an archived Builder timeout, not accepting work.

The recovery route already asks this review to classify retained partial work.
It may precede expensive regression execution; it grants no proof or completion
authority. Missing or stale bindings retain ordinary verification behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def builder_timeout_inspection(state: RunState, source_revision: str) -> bool:
    """Whether this exact source/task/attempt still awaits its failure inspection."""
    recovery = state.get("recovery_context") or {}
    task, contract = state.get("current_task") or {}, state.get("goal_contract") or {}
    history = state.get("automatic_timeout_recoveries") or []
    if (
        state.get("status") != "RUNNING"
        or state.get("next_stage") != "astra_review"
        or any(state.get(key) for key in ("active_stage", "uncertain_artifacts", "pending_report_repair"))
        or recovery.get("stage") != "terra"
        or recovery.get("role") != "terra"
        or recovery.get("next_stage") != "astra_review"
        or recovery.get("source_revision") != source_revision
        or not recovery.get("attempt_id")
        or not task.get("id")
        or task.get("kind") != "implement"
        or recovery.get("task_id") != task["id"]
        or not contract.get("hash")
        or task.get("contract_hash") != contract["hash"]
        or task.get("contract_revision") != contract.get("revision")
        or not history
        or history[-1] != recovery
    ):
        return False
    # Runner observations may follow the archive. Any later model attempt makes
    # this an ordinary review again, even if an obsolete recovery note survived.
    record: dict = next((row for row in reversed(state.get("stages") or []) if not row.get("runner_owned")), {})
    iteration, output = record.get("iteration"), record.get("output")
    return (
        record.get("stage") == "terra"
        and record.get("role") == "terra"
        and record.get("timed_out") is True
        and record.get("abandoned") is True
        and record.get("automatic_recovery") is True
        and record.get("rejected") is True
        and not record.get("report_only")
        and record.get("source_revision") == source_revision
        and record.get("task_id") == task["id"]
        and record.get("contract_hash") == contract["hash"]
        and record.get("contract_revision") == contract.get("revision")
        and type(iteration) is int
        and isinstance(output, str)
        and bool(output)
        and recovery["attempt_id"] == f"{iteration:03d}/{Path(output).stem}"
    )
