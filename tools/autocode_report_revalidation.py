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
    from . import autocode_report_repair_queue as report_repair_queue
    from . import autocode_report_revalidation_binding as revalidation_binding
    from . import autocode_resolve_repair_job as resolve_repair_job
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_repaired_result as repaired_result
    import autocode_report_repair_queue as report_repair_queue
    import autocode_report_revalidation_binding as revalidation_binding
    import autocode_resolve_repair_job as resolve_repair_job
    import autocode_source_scope as source_scope
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

    Old pending repairs without a recorded handoff binding cannot be authenticated
    retrospectively and stay on the ordinary recovery path.
    """
    pending = resolver_request.get("pending_report_repair") if resolver_request else state.get("pending_report_repair")
    if not isinstance(pending, dict) or any(
        state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts")
    ):
        return False
    original = pending.get("original") or {}
    if not isinstance(original, dict):
        return False
    expected_stage = resolve_repair_job.STAGE if resolver_request else original.get("stage")
    if state.get("next_stage") != expected_stage or not original.get("source_revision"):
        return False
    if pending.get("revalidation_context") != revalidation_binding.context(state, original["stage"]):
        return False
    fingerprint = util.runtime_source_digest()
    if pending.get("validation_runtime") == fingerprint:
        return False
    record = pending.get("latest_rejected") or original
    pins = pending.get("pins") or {}
    if not isinstance(pins, dict):
        return False
    if not isinstance(record, dict) or not record.get("rejected") or record.get("exit_code") != 0:
        return False
    if record is not original and (
        not record.get("report_only")
        or record.get("original_stage", str(record.get("stage", "")).removesuffix("_report_repair"))
        != original["stage"]
        or record.get("iteration") != original.get("iteration")
        or record.get("source_revision") != original.get("source_revision")
        or record.get("contract_hash") != original.get("contract_hash")
    ):
        return False
    if any(record.get(key) for key in ("timed_out", "interrupted", "truncated_output", "first_reply_budget")):
        return False
    required = [(original, key) for key in ("output", "events", "schema", "before_ref", "after_ref")]
    required.extend((record, key) for key in ("output", "events", "schema"))
    if (
        any(not isinstance(row.get(key), str) or not row[key] or row[key] not in pins for row, key in required)
        or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")
        or source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)["revision"]
        != original["source_revision"]
        or any(not Path(path).is_file() or runtime.support.file_hash(path) != digest for path, digest in pins.items())
        or not runtime.stage_completed(state, record)
    ):
        return False

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
