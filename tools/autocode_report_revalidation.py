"""Revalidate an authenticated rejected report after the validator changes."""

from __future__ import annotations

import copy
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_repaired_result as repaired_result
    from . import autocode_report_correction as report_correction
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_resolve_repair_job as resolve_repair_job
    from . import autocode_util as util
except ImportError:
    import autocode_repaired_result as repaired_result
    import autocode_report_correction as report_correction
    import autocode_report_repair_queue as report_repair_queue
    import autocode_resolve_repair_job as resolve_repair_job
    import autocode_util as util


def prepare_repair(runtime, state: RunState, run_dir, workspace):
    """Validate repair ownership and give a changed validator its saved report first."""
    pending = state["pending_report_repair"]
    original = pending["original"]
    if state.get("next_stage") != original.get("stage"):
        raise runtime.support.Paused(
            "PAUSED_STALE_REPORT_ROUTE", "Saved report repair belongs to a different stage; reconcile before retrying"
        )
    if revalidate(runtime, state, run_dir, workspace):
        return False
    if pending["attempts"] >= report_repair_queue.effective_repair_limit(state, pending):
        raise runtime.support.Paused("PAUSED_REPORT_REPAIR_LIMIT", "Bounded report-only repair attempts exhausted")
    return True


def revalidate(runtime, state: RunState, run_dir, workspace, *, resolver_request=None):
    """Apply only the same completed attempt under its pinned evidence and normal gates.

    Whether the saved report is still that evidence is decided once, by
    ``autocode_report_correction.authenticate``. Old pending repairs without a recorded
    handoff binding cannot be authenticated retrospectively and stay on the ordinary
    recovery path.
    """
    pending = resolver_request.get("pending_report_repair") if resolver_request else state.get("pending_report_repair")
    if not isinstance(pending, dict):
        return False
    fingerprint = util.runtime_source_digest()
    if pending.get("validation_runtime") == fingerprint:
        return False
    record = report_correction.authenticate(
        runtime, state, workspace, pending, stage=resolve_repair_job.STAGE if resolver_request else None
    )
    if record is None:
        return False
    pins = pending["pins"]

    # Report loading can normalize its output. Work on a private copy so a
    # failed validation never changes the archived bytes or their pin.
    scratch = Path(tempfile.mkdtemp(prefix="revalidated-report-", dir=run_dir))
    candidate = copy.deepcopy(record)
    candidate["output"] = str(scratch / Path(record["output"]).name)
    shutil.copyfile(record["output"], candidate["output"])
    if runtime.support.file_hash(candidate["output"]) != pins[record["output"]]:
        shutil.rmtree(scratch)
        raise runtime.support.Paused("PAUSED_STALE_VALIDATION", "The saved report changed while copying it")
    candidate.pop("reported_output", None)
    try:
        repaired_result.accept(
            runtime, state, run_dir, workspace, None, candidate, revalidate=True, resolver_request=resolver_request
        )
    except runtime.support.Paused:
        raise
    except repaired_result.RevalidationRejected as error:
        shutil.rmtree(scratch)
        pending["validation_runtime"] = fingerprint
        pending.setdefault("revalidations", []).append(
            {
                "runtime": fingerprint,
                "output": record["output"],
                "result": "rejected",
                "reason": str(error),
                "at": runtime.now(),
            }
        )
        runtime.write_json(Path(run_dir) / "state.json", state)
        return False
    return True


def revalidate_resolver(runtime, state: RunState, run_dir, workspace):
    """Recheck an unlaunched Resolver handoff before spending on that diagnosis."""
    request = state.get("resolve_repair_request")
    if (
        state.get("status") != "RUNNING"
        or state.get("next_stage") != resolve_repair_job.STAGE
        or not isinstance(request, dict)
        or request.get("kind") != "report_repair"
        or request.get("in_force")
        or resolve_repair_job.launched(state, request)
        or any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
    ):
        return False
    pending = request.get("pending_report_repair")
    if not isinstance(pending, dict):
        return False
    original = pending.get("original")
    if not isinstance(original, dict) or any(
        request.get(key) != expected
        for key, expected in (
            ("stage", original.get("stage")),
            ("previous_next_stage", original.get("stage")),
            ("original_output", original.get("output")),
            ("source_revision", original.get("source_revision")),
            ("contract_hash", pending.get("contract_hash") or (state.get("goal_contract") or {}).get("hash")),
        )
    ):
        return False
    return revalidate(runtime, state, run_dir, workspace, resolver_request=request)
