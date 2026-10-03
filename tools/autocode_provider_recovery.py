"""Bounded recovery of local provider failures before a turn starts.

Backend-neutral: classify the transport, not an engine/model name. Receipts live
on existing stages; this module reads them for the run-wide retry bound. Existing
user_events and the automatic recovery counter retain the audit and budget.
Runtime services are passed in so this module does not import the controller.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

MAX_STARTUP_RETRIES = 2
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOCK = re.compile(r"(?:Error: Unexpected error\s+)?"
                  r"(?:(?:Error|SQLiteError|SqliteError|SQLITE_BUSY|SQLITE_LOCKED):\s*)?"
                  r"database(?: table)? is locked\.?", re.I)


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Ambiguous duplicate transport fields")
    return result


def startup_lock(text):
    """Accept only an entire startup diagnostic, never tool/model text.

    Any session, usage, tool, text, terminal completion or unknown output makes
    the attempt uncertain. Error-only JSON has the same rule across adapters.
    """
    text = ANSI.sub("", text).strip()
    if LOCK.fullmatch(text):
        return True
    try:
        row = json.loads(text, object_pairs_hook=unique_object)
    except ValueError:
        return False
    if (not isinstance(row, dict) or row.get("type") not in ("error", "turn.failed")
            or set(row) - {"type", "error"}):
        return False
    error = row.get("error")
    if not isinstance(error, dict) or set(error) - {"message", "code"}:
        return False
    return (error.get("code") in (None, "SQLITE_BUSY", "SQLITE_LOCKED")
            and isinstance(error.get("message"), str)
            and bool(LOCK.fullmatch(error["message"].strip())))


def recover_startup(runtime, state, run_dir, workspace, error, *, sleep=time.sleep):
    """Retry at most twice after a stopped, unchanged failure with no observed turn.

    No model/billing/permission switch, session rotation, timeout extension or
    planning allowance refund. Report repair and uncertain execution keep their
    existing reconciliation paths. Pause/admission guards run before any retry.
    """
    record = state.get("active_stage") or {}
    run_dir = Path(run_dir)
    if (error.status != "PAUSED_PROVIDER_UNCERTAIN" or state.get("status") != "RUNNING"
            or not record.get("finished_at") or type(record.get("exit_code")) is not int
            or record["exit_code"] <= 0 or not record.get("processes")
            or not isinstance(record.get("duration_seconds"), (int, float))
            or not 0 <= record["duration_seconds"] <= 5
            or (record.get("activity") or {}).get("active_tool_count")
            or (record.get("activity") or {}).get("completed_tool_count")
            or record.get("timed_out") or record.get("interrupted") or record.get("cleanup_error")
            or record.get("report_only") or state.get("pending_report_repair")
            or state.get("uncertain_artifacts") or state.get("pending_questions")
            or state.get("pause_requested") or (run_dir / "pause-requested").exists()
            or (run_dir / "active-processes.json").exists()):
        return False
    request = state.get("user_request") or (state.get("agent_request") or {}).get("request")
    if request and request.get("kind") != "none":
        return False
    try:
        log = Path(record["events"])
        before_path = Path(record["before_ref"])
        output = Path(record["output"])
        if (not before_path.is_file() or log.stat().st_size > 65536
                or any(p.exists() for p in (output, output.with_suffix(".reported.json"),
                                            output.with_suffix(".response.txt")))
                or not startup_lock(log.read_text(errors="replace"))):
            return False
        runtime.assert_stage_stopped(record)
        before = runtime.read_json(before_path)
        after = runtime.support.snapshot(workspace)
        if not before.get("revision") or before["revision"] != after["revision"]:
            return False
    except (OSError, ValueError, KeyError, runtime.support.Paused):
        return False
    retries = sum(bool(r.get("startup_recovery")) for r in state.get("stages", []))
    if retries >= MAX_STARTUP_RETRIES:
        raise runtime.support.Paused("PAUSED_PROVIDER_UNCERTAIN",
            f"Provider startup retry limit reached ({MAX_STARTUP_RETRIES}); the local database remains locked. "
            "Attempts are retained; no further provider calls will launch automatically.")
    runtime.timeout_recovery_guard(state)
    runtime.account_stage(state, record)
    after_path = output.with_suffix(".after.json")
    runtime.write_json(after_path, after)
    delay = 2 ** retries
    reason = "Provider local database was locked with no observed turn activity; unchanged workspace and stopped processes verified"
    record.update(after_ref=str(after_path), source_revision=after["revision"], changed_files=[],
                  abandoned=True, automatic_recovery=True,
                  startup_recovery={"kind": "local_database_lock", "retry_number": retries + 1,
                                    "delay_seconds": delay})
    originals = runtime.archive_rejected_stage(state, run_dir, record, reason)
    runtime.count_automatic_recovery(state)
    state.setdefault("user_events", []).append({"kind": "automatic_provider_startup_recovery",
        "actor": "runner", "at": runtime.now(), "stage": record["stage"],
        "events": record["events"], "retry_number": retries + 1, "delay_seconds": delay})
    runtime.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    print(f"{record['stage']}: provider startup database lock; retry {retries + 1}/{MAX_STARTUP_RETRIES} "
          f"after {delay}s; failed attempt retained", flush=True)
    sleep(delay)
    return True


def recover_dispatch(runtime, state, run_dir, workspace, error):
    """The controller's recovery boundary; other recovery policies are unchanged."""
    return (recover_startup(runtime, state, run_dir, workspace, error)
            or runtime.automatically_recover_truncated_review(state, run_dir, workspace, error)
            or runtime.automatically_recover_timed_out_stage(state, run_dir, workspace, error)
            or runtime.automatically_recover_external_directory_denial(state, run_dir, workspace, error))
