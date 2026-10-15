"""Commit parent candidates at the existing intervention and persistence boundary.

The caller supplies admission, input guards and the durable sink. This module
owns replacement/rollback behavior without importing the controller or runner.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def commit_user_action(
    state: RunState,
    candidate,
    run_dir,
    *,
    require_current_inputs,
    save,
    admission,
    guard_inputs,
    commit_pending,
    native=None,
    report_changes=None,
):
    """Commit a prepared answer/approval without accepting earlier queued input."""
    if run_dir is None:  # Pure in-memory callers have no concurrent inbox.
        cast(dict, state).clear()
        state.update(candidate)
        return
    previous_settings = state.get("settings")
    approval = native.approval if native is not None else None
    with native.sink.boundary(approval=approval) if native is not None else nullcontext():
        with admission(run_dir):
            if require_current_inputs:
                try:
                    guard_inputs(candidate, Path(candidate["workspace"]), run_dir)
                except ValueError as error:
                    raise util.Paused("PAUSED_STALE_VALIDATION", str(error)) from error
            try:
                commit_pending(candidate, run_dir, lambda value: save(run_dir / "state.json", value))
            except util.JSONCommitUncertain as error:
                if error.replaced:
                    cast(dict, state).clear()
                    state.update(error.candidate)
                raise
            cast(dict, state).clear()
            state.update(candidate)
    if approval is not None:
        native.sink.publish(state, approval=approval)
        native.approval = None
    if report_changes is not None:
        report_changes(previous_settings, state.get("settings"))


def commit_boundary_candidate(
    state: RunState,
    candidate,
    run_dir,
    workspace,
    *,
    save,
    serialized,
    pending,
    consume,
    commit_pending,
    native=None,
    planning_record=None,
):
    """Publish normal and repaired reports through the same intervention boundary."""
    # apply_result has already created these exact process-local sidecars.
    # Snapshot the full tuple before any parent write, never reconstruct it from
    # serialized accepted flags or discover new evidence after persistence.
    publications = native.sink.factory.evidence_publications() if native is not None and planning_record is None else ()
    with (
        native.sink.boundary(planning_record=planning_record, publications=publications)
        if native is not None
        else nullcontext()
    ):
        with serialized(run_dir):
            # Validate inbox readability before changing the owner state. A corrupt
            # inbox leaves the terminal stage available for explicit recovery.
            pending(run_dir)
            original = deepcopy(state)

            def commit(value):
                state.clear()
                state.update(value)
                try:
                    if not consume(state, run_dir, workspace, lock_held=True, persist=save):
                        save(run_dir / "state.json", state)
                except util.JSONCommitUncertain as error:
                    if error.replaced:
                        state.clear()
                        state.update(error.candidate)
                    raise
                except Exception as error:
                    if native is not None:
                        native.sink.preserve_durable_failure(error, phase="boundary_side_effect", owner_state=state)
                    state.clear()
                    state.update(original)
                    raise

            commit_pending(candidate, run_dir, commit)
    if planning_record is not None:
        native.sink.publish(state, planning_record=planning_record)
    elif publications:
        native.sink.publish(state, publications=publications)
