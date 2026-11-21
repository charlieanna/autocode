"""Read-only admission for a bounded Tester report retry."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_repair_provenance as repair_provenance
    from . import autocode_report_repair_queue as records
    from . import autocode_report_retry as report_retry
    from . import autocode_report_source as report_source
    from . import autocode_resolver_human as resolver_human
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_roles as planning
    from . import autocode_stage_stalls as stage_stalls
    from . import autocode_support as support
    from . import autocode_util as util
except ImportError:
    import autocode_repair_provenance as repair_provenance
    import autocode_report_repair_queue as records
    import autocode_report_retry as report_retry
    import autocode_report_source as report_source
    import autocode_resolver_human as resolver_human
    import autocode_source_scope as source_scope
    import autocode_stage_roles as planning
    import autocode_stage_stalls as stage_stalls
    import autocode_support as support
    import autocode_util as util


def reroute_abandoned_tester(state: RunState, stage):
    return (
        stage == "tester"
        and state.get("next_stage") == "review_completion"
        and not state.get("validation")
        and (state.get("recovery_context") or {}).get("role") == "tester"
        and any(
            record.get("stage") == "tester_report_repair"
            and record.get("abandoned")
            and util.attempt_id(record) == cast(dict, state["recovery_context"]).get("attempt_id")
            for record in state.get("stages", [])
        )
    )


def preparable(state: RunState, *, allow_repeated=False):
    """Whether the saved report retry can be prepared without changing state."""
    if state.get("status") not in (
        "PAUSED_REPORT_REPAIR_LIMIT",
        "PAUSED_REPORT_REPAIR_INPUT",
        "PAUSED_INVALID_OUTPUT",
        "PAUSED_REPEATED_FAILURE",
    ) or state.get("active_stage"):
        return False
    # A repair whose saved inputs exceed the handoff limits can never launch: it is spent.
    unlaunchable = state["status"] == "PAUSED_REPORT_REPAIR_INPUT" or stage_stalls.exhausted_unlaunchable_pending(state)
    pending = state.get("pending_report_repair")
    original = pending.get("original") if isinstance(pending, dict) else None
    if not isinstance(original, dict):
        return False
    stage = original.get("stage")
    if (
        unlaunchable
        and cast(dict, pending).get("attempts", 0) < records.repair_limit(state)
        and report_source.has_repairable_empty_latest(pending)
    ):
        # A hold saved before #2426 covered execution: the remaining repair still has its
        # original input, so keep it instead of discarding the stage's work.
        return False
    return not (
        (stage != state.get("next_stage") and not reroute_abandoned_tester(state, stage))
        or stage == "draft_plan"
        or planning.is_planning(state, stage)
        or not (
            unlaunchable
            or cast(dict, pending).get("attempts") == records.repair_limit(state)
            or allow_repeated
            and report_retry.bounded_failure(state, records.repair_limit(state))
        )
    )


def stale_report_repair(state: RunState, workspace, *, revision=None):
    """(repaired source, current source) when only the source moved under a queued report repair, else None.

    A repair restates the original report for the source that attempt ran on, so after an operator
    edit it can never run (issue #302). A changed goal or changed pinned evidence is not this case.
    Neither is a run waiting on a person: an AutoResolver operational request, even one a source edit
    made stale, is answered or withdrawn only through its own actions, never as a side effect of
    resuming (archiving would also change the frontier the request is bound to)."""
    pending = state.get("pending_report_repair")
    status = str(state.get("status") or "")
    if (
        not isinstance(pending, dict)
        or any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
        or not (status == "RUNNING" or status.startswith("PAUSED_"))
        or (state.get(resolver_human.PUBLIC) or {}).get("scope") in ("operational_exhaustion", "blocker")
        or state.get(resolver_human.PRIVATE)
    ):
        return None
    original = pending.get("original") or {}
    checked = original.get("source_revision")
    if (
        not checked
        or original.get("stage") != state.get("next_stage")
        or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")
    ):
        return None
    if revision is None:
        revision = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
    if checked == revision or any(
        not Path(p).is_file() or support.file_hash(p) != h for p, h in pending.get("pins", {}).items()
    ):
        return None
    return checked, revision


def _rejected_repair_reports(state: RunState, original):
    """The reports of ``original``'s earlier rejected repairs: a later repair may have edited one of them."""
    return [
        row.get("output")
        for row in state.get("stages", [])
        if row.get("report_only") and row.get("rejected") and row.get("original_stage") == original.get("stage")
    ]


def report_retry_frontier(state: RunState, workspace, selected, pending):
    """The original source/schema/pin/bound checks over one retained repair input."""
    original = pending.get("original") or {}
    # A fresh attempt the failure bound stopped before any repair is retried itself, never an earlier repair.
    repair = (
        original
        if not pending.get("latest_rejected") and report_retry.rejected_attempt(state, pending=pending)
        else next(
            (
                row
                for row in reversed(state.get("stages", []))
                if row.get("report_only") and row.get("rejected") and row.get("original_stage") == original.get("stage")
            ),
            None,
        )
    )
    if (
        any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
        or not report_retry.retryable(pending.get("error"))
        or original.get("stage") != "tester"
        or not repair
        or selected != util.attempt_id(repair)
        or (repair.get("original_stage") or repair.get("stage")) != original.get("stage")
        or repair.get("source_revision") != original.get("source_revision")
        or not repair.get("schema")
        or not original.get("schema")
        or not Path(repair["schema"]).is_file()
        or not Path(original["schema"]).is_file()
        or not repair_provenance.same_schema(original, repair, reports=_rejected_repair_reports(state, original))
        or not report_retry.bounded_failure_evidence(state, pending, records.repair_limit(state))
    ):
        raise ValueError("--retry-report must match the bounded rejected report-only attempt")
    revision = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
    if pending is state.get("pending_report_repair") and stale_report_repair(state, workspace, revision=revision):
        raise ValueError(
            "The source changed after this report was rejected, so it cannot be retried; "
            "use --resume-paused to archive the stale repair and validate the current source afresh"
        )
    if (
        revision != original["source_revision"]
        or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")
        or any(not Path(p).is_file() or support.file_hash(p) != h for p, h in pending.get("pins", {}).items())
    ):
        raise ValueError("Saved report inputs changed; reconcile them before retrying")
    return {
        "attempt": selected,
        "pending": support.digest(pending),
        "repair": support.digest(repair),
        "source_revision": revision,
        "contract_hash": (state.get("goal_contract") or {}).get("hash"),
        "schemas": {path: support.file_hash(path) for path in {repair["schema"], original["schema"]}},
        "pins": copy.deepcopy(pending.get("pins") or {}),
    }


def validate(state: RunState, workspace, selected):
    """Check the same saved frontier and preparation constraints as the retry executor."""
    if state.get("status") != "PAUSED_REPEATED_FAILURE":
        raise ValueError("--retry-report must match the bounded rejected report-only attempt")
    pending = state.get("pending_report_repair") or {}
    frontier = report_retry_frontier(state, workspace, selected, pending)
    if not preparable(state, allow_repeated=True):
        raise ValueError("Saved stage cannot be retried as a fresh execution report")
    return frontier
