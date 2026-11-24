"""Where a finished or replaced build task goes: the run's ``task_archive`` (#695).

Five lifecycle steps (a new contract revision, a follow-up turn, a Resolver replan, a bug-fix plan sent
back and the next task's assignment) each moved ``current_task`` there with their own copy of the same
lines. They read this one rule, which keeps a task the run never had (None or empty) out of the archive,
so every reader of ``task_archive`` holds only task records.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


def archive_current(state: RunState, *, keep: bool = False) -> bool:
    """Append the current task to ``task_archive``; with ``keep`` a copy, leaving it current, else it moves.

    Returns whether there was a task to archive."""
    task = state.get("current_task")
    if not task:
        return False
    if keep:
        state.setdefault("task_archive", []).append(copy.deepcopy(task))
    else:
        del state["current_task"]
        state.setdefault("task_archive", []).append(task)
    return True
