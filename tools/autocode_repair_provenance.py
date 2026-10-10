"""Read-only original execution witnesses for accepted planning reports."""

import hashlib
import json
from pathlib import Path


def verify_accepted_repair(state, accepted):
    """Read the repaired-output hash saved by accept_repaired_report, never repin it."""
    receipt = next(
        (
            row
            for row in state.get("report_repair_history", [])
            if row.get("result") == "accepted" and row.get("repair") == accepted
        ),
        None,
    )
    try:
        if (
            not receipt
            or type(receipt.get("output_hash")) is not str
            or hashlib.sha256(Path(accepted["output"]).read_bytes()).hexdigest() != receipt["output_hash"]
        ):
            raise ValueError("accepted repair output hash is missing or changed")
    except (OSError, KeyError, TypeError) as error:
        raise ValueError("accepted repair output hash cannot be verified") from error
    return receipt


def witness(state, stage, output):
    accepted = next(
        (
            row
            for row in reversed(state.get("stages", []))
            if row.get("output") == output and (row.get("original_stage") or row.get("stage")) == stage
        ),
        None,
    )
    if (
        not accepted
        or type(accepted.get("exit_code")) is not int
        or accepted["exit_code"] != 0
        or accepted.get("rejected")
        or accepted.get("abandoned")
        or accepted.get("changed_files")
    ):
        raise ValueError("planning report lacks an accepted read-only witness")
    original = accepted
    if accepted.get("report_only"):
        if accepted.get("stage") != stage + "_report_repair" or accepted.get("original_stage") != stage:
            raise ValueError("planning repair does not identify its original stage")
        receipt = verify_accepted_repair(state, accepted)
        original = next(
            (
                row
                for row in state.get("stages", [])
                if receipt
                and row.get("output") == receipt.get("original_output")
                and row.get("events") == accepted.get("applied_original_events")
            ),
            None,
        )
    if (
        not original
        or original.get("stage") != stage
        or original.get("report_only")
        or type(original.get("exit_code")) is not int
        or original["exit_code"] != 0
        or original.get("abandoned")
        or original.get("changed_files")
        or not original.get("role")
        or not original.get("source_revision")
        or any(
            original.get(key) != accepted.get(key)
            for key in ("role", "source_revision", "iteration", "schema", "contract_hash", "criteria_revision")
        )
    ):
        raise ValueError("planning report lacks matching original provenance")
    try:
        if not Path(output).is_file() or not Path(original["output"]).is_file():
            raise ValueError("planning report artifact is missing")
        saved = original.get("thread_id")
        if saved or accepted.get("report_only"):
            observed = set()
            for line in Path(original["events"]).read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                identity = (
                    event.get("thread_id")
                    if event.get("type") == "thread.started"
                    else event.get("sessionID")
                    if event.get("type") in ("step_start", "step_finish", "tool_use", "text", "error")
                    else None
                )
                if identity is not None:
                    if type(identity) is not str or not identity:
                        raise ValueError("original planning session identity is malformed")
                    observed.add(identity)
            if (
                saved
                and (
                    type(saved) is not str
                    or observed != {saved}
                    or original.get("expected_session") not in (None, saved)
                )
            ) or (not saved and (original.get("supports_sessions") is not False or observed)):
                raise ValueError("planning report lacks authenticated original session evidence")
        elif original.get("supports_sessions") is True:
            raise ValueError("planning report is missing its original session")
    except (OSError, KeyError, TypeError) as error:
        raise ValueError("planning original evidence is missing or unreadable") from error
    return original, saved or output
