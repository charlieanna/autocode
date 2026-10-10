"""Explicit adoption of one inspected, owner-lost Investigator file report.

No saved success flag or provider exit is inferred. Inspection is read-only;
the operator token binds the exact bytes and admission evidence. Only apply()
writes record.report_recovery, retained in normal stage history as provenance.
Controller services are supplied rather than imported.
"""

import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_job_failure as job_failure
    from . import autocode_job_source as source
    from . import autocode_util as util
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_job_source as source
    import autocode_util as util

BEFORE_WRITE = ContextVar("inspected_job_report_before_write", default=None)


def _owned(path, expected):
    path, expected = Path(path), Path(expected)
    if path != expected or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Report recovery requires the exact owned non-symlink artifact path")
    return path


def inspect(runtime, state, run_dir, workspace):
    """Return a source/attempt/hash-bound offer, or reject without changing state."""
    record = state.get("active_stage") or {}
    if (
        record.get("stage") != "investigate_bug"
        or record.get("report_only")
        or record.get("output_mode") != "report_file"
        or record.get("supports_sessions") is not False
        or record.get("exit_code") is not None
        or record.get("rejected")
        or record.get("timed_out")
        or record.get("cleanup_error")
        or state.get("active_runner_check")
        or record.get("expected_session")
        or state.get("status") == "PAUSED_PROCESS_CLEANUP"
        or (state.get("workflow") or {}).get("kind") != "bugfix"
    ):
        raise ValueError("Only a current owner-lost Investigator file report can be recovered")
    if runtime.stop_policy.applied_stop(state):
        raise ValueError("An applied Stop cannot be bypassed by report adoption")
    attempt = 1 + sum(
        row.get("stage") == record["stage"] and row.get("iteration") == record["iteration"]
        for row in state.get("stages", [])
    )
    base = runtime.artifacts.stage_base(Path(run_dir), record["iteration"], record["stage"], attempt)
    report = _owned(record["output"], base.with_suffix(".json"))
    events = _owned(record["events"], base.with_suffix(".jsonl"))
    before_path = _owned(record["before_ref"], base.with_suffix(".before.json"))
    capture = record.get("job_source") or {}
    capture_path = _owned(capture["capture"], base.with_suffix(".source.json"))
    if util.file_hash(capture_path) != capture.get("capture_hash"):
        raise ValueError("Source capture changed or is corrupt")
    original = util.read_object(capture_path)["original"]
    if util.digest(original) != capture.get("before_identity"):
        raise ValueError("Source capture identity changed")
    if not source.matches_original(
        workspace, capture.get("before_identity"), source_paths=capture.get("source_paths", ())
    ):
        raise ValueError("Source changed since Investigator admission")
    before = util.read_object(before_path)
    context = record.get("capture_context") or {}
    if (
        context.get("attempt") != str(report)
        or not context.get("nonce")
        or context.get("source_revision") != before.get("revision")
    ):
        raise ValueError("Report capture context does not identify this attempt and source")
    if (
        runtime.source_scope.snapshot(workspace, state, base_snapshot=runtime.support.snapshot)["revision"]
        != before["revision"]
    ):
        raise ValueError("Original source snapshot changed")
    if not record.get("job_configuration") or job_failure.configuration(state) != record["job_configuration"]:
        raise ValueError("Provider configuration or limits changed")
    runtime.launch_inputs.guard(state, workspace, run_dir)
    observation = runtime.supervision.observe(record.get("supervision"))
    receipt = observation.get("receipt") or {}
    _owned((record.get("supervision") or {})["receipt"], base.with_suffix(".supervision.json"))
    if receipt.get("phase") != "stopped" or receipt.get("cause") != "owner_lost" or receipt.get("cleanup_error"):
        raise ValueError("Owner-loss cleanup is not conclusively verified")
    for key in ("owner", "keeper", "provider"):
        live = observation.get(key) or {}
        if live.get("checked") is not True or live.get("alive") is not False:
            raise ValueError("Supervised workers are alive or their liveness is unknown")
    workers = runtime.processes.recorded_worker_state(record)
    if workers.get("checked") is not True or workers.get("alive") is not False:
        raise ValueError("Recorded workers are alive or their liveness is unknown")
    if runtime.processes.live_processes(receipt.get("processes") or []):
        raise ValueError("Receipt workers are still alive")
    runtime.assert_stage_stopped(record)
    schema_path = _owned(record["schema"], Path(run_dir) / "schemas" / "v3-investigate_bug.json")
    schema = util.read_object(schema_path)
    if schema != runtime.support.model_output_schema(runtime.jobs.bug_job.SCHEMA):
        raise ValueError("Report schema does not match the Investigator schema")
    report_bytes = report.read_bytes()
    runtime.support.validate_schema(json.loads(report_bytes), schema)
    binding = {
        "run": str(run_dir),
        "workspace": str(workspace),
        "attempt_id": runtime.attempt_id(record),
        "started_at": record["started_at"],
        "report": str(report),
        "sha256": hashlib.sha256(report_bytes).hexdigest(),
        "events_hash": util.file_hash(events),
        "capture_context": context,
        "capture_hash": capture["capture_hash"],
        "source_identity": capture["before_identity"],
        "before_hash": util.file_hash(before_path),
        "configuration": record["job_configuration"],
        "schema_hash": util.file_hash(schema_path),
        "supervision": record["supervision"],
        "receipt": receipt,
        "route": record.get("launch_route"),
        "engine": record.get("engine"),
        "workflow": state.get("workflow"),
        "answers": state.get("answers"),
        "launch_sources": state.get("launch_sources"),
        "task": state.get("task"),
        "contract": state.get("goal_contract"),
        "current_task": state.get("current_task"),
    }
    return {
        "token": "jrr:" + util.digest(binding),
        "attempt_id": binding["attempt_id"],
        "report": str(report),
        "sha256": binding["sha256"],
        "source_identity": binding["source_identity"],
        "action": "--recover-job-report @stdin --authorization-stdin < /path/to/private-authorization.json",
        "authority": "explicit_operator_adoption",
        "warning": "Inspect these exact report bytes before adopting; the provider exit remains unknown.",
    }


def offer(runtime, state, run_dir, workspace):
    try:
        return inspect(runtime, state, run_dir, workspace)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        return None


def apply(runtime, state, run_dir, workspace, token):
    inspected = inspect(runtime, state, run_dir, workspace)
    if token != inspected["token"]:
        raise ValueError("Job report recovery token does not match the inspected report and attempt")
    # Charge the durable active attempt, not a speculative copy: a rejected probe
    # can checkpoint accounting, and a second adoption must not charge it again.
    runtime.account_stage(state, state["active_stage"])
    record = deepcopy(state["active_stage"])
    raw = Path(inspected["report"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != inspected["sha256"]:
        raise ValueError("Report changed before loading the inspected report")
    value = runtime.load_stage_report(record, workspace, state=state)
    # Loading may run evidence checks. Reinspect before any result/probe application.
    if value != json.loads(raw) or inspect(runtime, state, run_dir, workspace)["token"] != token:
        raise ValueError("Report or source changed while loading the inspected report")
    record.update(
        source_revision=record["capture_context"]["source_revision"],
        changed_files=[],
        recovered_at=util.now(),
        metrics=runtime.support.event_metrics(record["events"]),
    )
    record["report_recovery"] = {**inspected, "actor": "user_cli", "at": util.now(), "exit_code": None}

    def still_current():
        # Only this application's own tracked probe may be active here. Its
        # cleanup must return successfully before the unit invokes this guard.
        checking = {**state, "active_runner_check": None}
        if inspect(runtime, checking, run_dir, workspace)["token"] != token:
            raise ValueError("Report or source changed during the reproduction probe")

    guard = BEFORE_WRITE.set(still_current)
    try:
        runtime.commit_stage_result(state, record["stage"], value, record, workspace, run_dir)
    finally:
        BEFORE_WRITE.reset(guard)
    runtime.write_json(Path(run_dir) / "state.json", state)
