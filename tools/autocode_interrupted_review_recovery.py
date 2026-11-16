"""Settle a stopped source-read-only attempt without accepting its response.

The original launch binding and fresh supervisor cleanup must both hold. A
new attempt remains subject to the ordinary stage, model and spending gates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from pathlib import Path

try:
    from . import autocode_recovery_accounting as accounting
    from . import autocode_run_records as records
    from . import autocode_run_status as status
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_util as util
    from . import autocode_validation_recovery as validation
    from . import autocode_workflow as workflow
    from .autocode_interrupted_review_proof import proof
except ImportError:
    import autocode_recovery_accounting as accounting
    import autocode_run_records as records
    import autocode_run_status as status
    import autocode_stop as stop
    import autocode_support as support
    import autocode_util as util
    import autocode_validation_recovery as validation
    import autocode_workflow as workflow
    from autocode_interrupted_review_proof import proof


def settle(runner, state: RunState, run_dir, workspace, inspection):
    """Archive once under the writer lock, keeping usage, route and current evidence."""
    with runner.interventions.admission(run_dir):
        return _settle(runner, state, run_dir, workspace, inspection)


def _settle(runner, state: RunState, run_dir, workspace, inspection):
    run_dir = Path(run_dir)
    if stop.pending_stop(run_dir) is not None or (run_dir / "pause-requested").exists():
        return False
    evidence = proof(state, workspace, inspection)
    if evidence is None:
        return False
    record = cast(dict, state["active_stage"])  # The proof checked the active record under the writer lock.
    selected = records.attempt_id(record)
    stage = record["stage"]
    reason = (
        "Controller stopped during a read-only attempt; verified cleanup and unchanged inputs permit a fresh attempt"
    )
    after_path = Path(record["output"]).with_suffix(".after.json")
    util.atomic_json(after_path, evidence["source"])
    record.update(
        metrics=support.event_metrics(record["events"]),
        after_ref=str(after_path),
        source_revision=evidence["source"]["revision"],
        changed_files=[],
        abandoned=True,
        automatic_recovery=True,
        interrupted=True,
    )
    records.account_stage(state, record)
    next_stage = stage
    if stage in ("review_completion", "review_checkpoint") and not validation.current(state, record):
        next_stage = validation.permission_retry(state, record, review_stage=workflow.review_stage(state))
    originals = records.archive_rejected_stage(state, run_dir, record, reason)
    state.setdefault("sessions", {}).pop(record.get("route_role", record["role"]), None)
    accounting.count(state)
    recovery = {
        "at": util.now(),
        "attempt_id": selected,
        "stage": stage,
        "role": record["role"],
        "source_revision": evidence["source"]["revision"],
        "changed_files": [],
        "events": record["events"],
        "source_snapshot": record["after_ref"],
        "launch_binding": evidence["launch_binding"],
        "next_stage": next_stage,
        "instruction": reason
        + ". Inspect retained work; its unfinished response is not evidence of success. Keep the saved model and limits.",
    }
    state["recovery_context"] = recovery
    state.setdefault("user_events", []).append(
        {
            "kind": "automatic_interruption_recovery",
            "actor": "runner",
            "at": recovery["at"],
            "attempt_id": selected,
            "stage": stage,
            "next_stage": next_stage,
            "reason": reason,
        }
    )
    status.running(state, "PLANNING" if record.get("planning") else "EXECUTING", next_stage=next_stage)
    runner.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True
