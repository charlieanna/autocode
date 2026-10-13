"""Saved operator limits for an explicitly admitted parallel Builder retry.

Only the parent retry admission calls assign_selected. Automatic worker recovery
and recursive execution never adopt settings from another run. The copy changes
future bounds and their ownership, never routes, recorded deadlines or accounting.
"""

from __future__ import annotations

import copy
from contextlib import ExitStack
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


BOUNDS = (
    ("limits", "tool_timeout_seconds", "tool_timeout_seconds"),
    ("limits", "stage_timeout_seconds", "stage_timeout_seconds"),
    ("milestone_checkpoints", "max_seconds", "milestone_max_seconds"),
)


def updated_settings(child, parent):
    """Copy only saved, explicitly owned retry bounds; preserve both inputs."""
    result = copy.deepcopy(child)
    origins = parent.get("budget_origins") or {}
    for section, key, origin in BOUNDS:
        source = parent.get(section) or {}
        if origins.get(origin) == "user_explicit" and key in source:
            result.setdefault(section, {})[key] = copy.deepcopy(source[key])
            result.setdefault("budget_origins", {})[origin] = "user_explicit"
    return result


def assign_selected(state, run_dir, rows, *, persist):
    """Bind settings to all selected stopped children before the parent records its retry.

    Admission has already checked approval, worker liveness and saved results.
    Lock and validate the entire selection before saving any child, so a busy or
    stale later member cannot partially change the rejected selection.
    """
    settings = state.get("settings") or {}
    if updated_settings({}, settings) == {}:
        return
    batch = state.get("orchestration_batch") or {}
    contract = state.get("goal_contract") or {}
    parent_workspace = Path(state["workspace"]).resolve()
    if batch.get("status") != "BUILDING" or batch.get("contract_hash") != contract.get("hash"):
        raise ValueError("Builder retry limits require the current approved batch")
    selected = {row["milestone_id"]: row for row in rows}
    with ExitStack() as locks:
        updates = []
        for row in selected.values():
            workspace, directory = Path(row["workspace"]).resolve(), Path(row["run_dir"]).resolve()
            if (
                row not in batch.get("workers", [])
                or not workspace.is_relative_to(parent_workspace)
                or not directory.is_relative_to(workspace / ".autocode" / "runs")
            ):
                raise ValueError(f"Builder {row['milestone_id']}'s retry limits require its current child paths")
            locks.enter_context(util.workspace_lock(workspace))
            child = util.read(directory / "state.json")
            goal = child.get("goal_contract") or {}
            if (
                Path(child.get("workspace") or "/").resolve() != workspace
                or Path(child.get("parent_run") or "/").resolve() != Path(run_dir).resolve()
                or child.get("parent_batch") != batch.get("id")
                or child.get("current_task") != row.get("task")
                or goal.get("approval_status") != "approved"
                or goal.get("hash") != contract.get("hash")
                or goal.get("revision") != contract.get("revision")
                or util.digest({key: goal.get(key) for key in ("task_id", "revision", "body")}) != goal.get("hash")
            ):
                raise ValueError(
                    f"Builder {row['milestone_id']}'s retry limits require its current parent batch and task"
                )
            previous = child.get("settings") or {}
            replacement = updated_settings(previous, settings)
            if replacement != previous:
                child["settings"] = replacement
                updates.append((directory / "state.json", child))
        for path, child in updates:
            persist(path, child)
