"""The revision a run started from (``base_commit``), which the regression proof and a package run's
check for changes outside its package (autocode_package_writes) both compare the candidate with.

Moved out of autocode_regression so a module below it can read the base without importing the proof.
Reads ``base_commit`` (written at launch by autocode_run_setup, or by the build loop for a run that
found its checkout busy) and the first stage record's ``before_ref``; writes nothing.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_util as util
    import autocode_workspaces as workspaces


def base_commit(state: RunState, workspace):
    """The revision the run started from: saved at run creation, else the task worktree's base,
    else the HEAD its first stage recorded (runs created before base_commit was saved)."""
    if state.get("base_commit"):
        return state["base_commit"]
    try:
        saved = (workspaces.metadata(workspace) or {}).get("base_commit")
    except ValueError:
        saved = None
    return saved or _first_recorded_head(state, workspace)


def _first_recorded_head(state: RunState, workspace):
    first = next((record["before_ref"] for record in state.get("stages") or [] if record.get("before_ref")), None)
    try:
        start = util.read(first).get("head") if first else None
    except (OSError, ValueError, AttributeError):
        return None
    # A commit the current source no longer descends from (a rebase, a reset) cannot be its base.
    if (
        start
        and subprocess.run(
            ["git", "-C", str(workspace), "merge-base", "--is-ancestor", start, "HEAD"], capture_output=True
        ).returncode
        == 0
    ):
        return start
    return None
