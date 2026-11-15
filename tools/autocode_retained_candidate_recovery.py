"""Reconsider a saved no-progress candidate through fresh independent review."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_failure as builder_failure
    from . import autocode_retained_work as retained_work
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_builder_failure as builder_failure
    import autocode_retained_work as retained_work
    import autocode_source_scope as source_scope
    import autocode_util as util


def recover(state: RunState, workspace, *, bound_only=False, snapshot=util.snapshot, review_stage):
    """Reconsider an already-saved no-progress report without another provider call."""
    reports = state.get("no_progress_reports") or []
    if (
        state.get("active_stage")
        or state.get("uncertain_artifacts")
        or state.get("pending_report_repair")
        or not reports
        or (state.get("current_task") or {}).get("kind") != "implement"
    ):
        return False
    if bound_only and not any(state.get(key) for key in ("stuck_investigation", "builder_failure_hold")):
        return False
    revision = source_scope.snapshot(workspace, state, base_snapshot=snapshot)["revision"]
    record = next(
        (
            row
            for row in reversed(state.get("stages", []))
            if (row.get("original_stage") or row.get("stage")) == "builder"
            and not row.get("runner_owned")
            and not row.get("changed_files")
            and row.get("source_revision") == revision
        ),
        None,
    )
    if not record:
        return False
    value = reports[-1]
    request = builder_failure.reconsiderable(state, record, workspace) if bound_only else None
    if bound_only and not request:
        return False
    evidence = retained_work.validated_candidate(state, value, record, workspace, revision)
    if not evidence:
        evidence = retained_work.fresh_candidate(state, record, revision)
        # Only authenticated independent writer progress extends saved recovery.
        # Other retained-work policies keep their existing admission paths.
        if not evidence or not evidence.get("accepted_test_writer"):
            return False
        actual = util.read_object(record["output"])
        if any(value.get(key) != val for key, val in actual.items()):
            return False
    if not evidence:
        return False
    state["implementation"] = {
        **copy.deepcopy(value),
        "source_revision": evidence["source_revision"],
        "workspace": str(workspace),
    }
    paths = evidence.get("retained_paths", []) if evidence.get("accepted_test_writer") else []
    state["changed_files"] = paths
    if paths:
        cast(dict, state["implementation"])["changed_files"] = paths
    state["source_snapshot"] = record.get("after_ref")
    state["diff_ref"] = record.get("diff_ref")
    state["next_stage"] = review_stage(state)
    state["no_progress_batches"] = 0
    if request:
        builder_failure.retire_reconsidered(state, request)
    state.setdefault("retained_candidate_handoffs", []).append(
        {
            "at": util.now(),
            "task_id": cast(dict, state["current_task"])["id"],
            "builder_output": record["output"],
            "reconsidered": True,
            "previously_validated": not bool(evidence.get("accepted_test_writer")),
            **evidence,
        }
    )
    return True
