"""Which milestones the current task covers, and whether the saved validation still proves it.

autocode_milestones re-exports these. autocode_findings uses them to decide which blocking findings
a milestone owns, so it imports them from here rather than from autocode_milestones, which imports
the findings ledger back. A ``goal_contract``, ``current_task`` or ``validation`` that RunState
allows to be None reads as absent. Imports only autocode_util at runtime; RunState is imported for
type checking.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as s
except ImportError:
    import autocode_util as s


def key(state: RunState, task=None):
    task = task if task is not None else (state.get("current_task") or {})
    contract_hash = (state.get("goal_contract") or {}).get("hash", "")
    if task.get("milestone_ids"):
        return f"{contract_hash}:batch:{s.digest(sorted(task['milestone_ids']))[:16]}"
    return f"{contract_hash}:{task.get('milestone_id', '')}"


def scope(state: RunState, task=None):
    task = task if task is not None else (state.get("current_task") or {})
    if task.get("milestone_ids"):
        members = [
            m
            for m in (state.get("goal_contract") or {}).get("body", {}).get("milestones", [])
            if m["id"] in task["milestone_ids"]
        ]
        return {
            "id": "batch:" + s.digest(sorted(task["milestone_ids"]))[:16],
            "milestone_ids": list(task["milestone_ids"]),
            "members": members,
            "objective": "; ".join(m["objective"] for m in members),
            "acceptance_criteria": list(dict.fromkeys(c for m in members for c in m["acceptance_criteria"])),
        }
    for milestone in (state.get("goal_contract") or {}).get("body", {}).get("milestones", []):
        if milestone["id"] == task.get("milestone_id"):
            return milestone
    # Existing sealed briefs may predate milestone definitions. Freeze the scope
    # of their existing bounded task, rather than inventing or approving a brief.
    saved = state.get("milestone_progress", {}).get(key(state, task))
    return {
        "id": task.get("milestone_id", ""),
        "objective": task.get("objective", ""),
        "acceptance_criteria": list(saved["acceptance_criteria"] if saved else task.get("acceptance_criteria", [])),
    }


def fresh_validation(state: RunState, current):
    val = state.get("validation") or {}
    contract = state.get("goal_contract") or {}
    pins = val.get("evidence_hashes", {})
    return bool(
        val.get("reviewer_role") == "sol"
        and pins
        and val.get("contract_hash") == contract.get("hash")
        and val.get("contract_revision") == contract.get("revision")
        and val.get("criteria_revision") == state.get("criteria_revision")
        and val.get("task_id") == (state.get("current_task") or {}).get("id")
        and val.get("source_revision") == current["revision"]
        and all(Path(p).is_file() and s.file_hash(p) == digest for p, digest in pins.items())
    )
