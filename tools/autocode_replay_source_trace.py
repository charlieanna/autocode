"""Relocate a sealed review only through its authenticated replay occurrence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

try:
    from . import autocode_brief_obligations as obligations
    from . import autocode_source_trace as source_trace
    from . import autocode_util as util
except ImportError:
    import autocode_brief_obligations as obligations
    import autocode_source_trace as source_trace
    import autocode_util as util


def _trace(report):
    value = report.get(source_trace.KEY)
    return value if isinstance(value, dict) else source_trace.DEFAULT_TRACE


def rebind_context(tape, state, context, dispatch):
    """Replace only review execution provenance, preserving every scope field.

    Original absolute paths are never opened. Their pins must identify exactly
    one earlier recorded review, and the fresh seal must verify against the
    replay of that occurrence. This grants no current check or completion proof.
    """
    result = copy.deepcopy(context)
    if result is not None and not isinstance(result, dict):
        raise ValueError("Replay diverged: archival context is malformed")
    expected = (result or {}).get("contract_body") or {}
    current = (state.get("goal_contract") or {}).get("body") or {}
    if not isinstance(expected, dict) or not isinstance(current, dict):
        raise ValueError("Replay diverged: archival contract is malformed")
    old, fresh = expected.get(source_trace.KEY), current.get(source_trace.KEY)
    if old is None and fresh is None:
        return result
    if not isinstance(old, dict) or not isinstance(fresh, dict):
        raise ValueError("Replay diverged: source-trace scope differs")
    original, actual = old.get("review"), fresh.get("review")
    if (
        not isinstance(original, dict)
        or set(original) != source_trace.REVIEW_KEYS
        or not isinstance(actual, dict)
        or set(actual) != source_trace.REVIEW_KEYS
        or not isinstance(original["stage"], str)
        or original["stage"] not in source_trace.REVIEW_STAGES
        or original["stage"] != actual["stage"]
        or original["trace_sha256"] != actual["trace_sha256"]
    ):
        raise ValueError("Replay diverged: source-trace review differs")
    target = next((row for row in tape.document["dispatches"] if row["id"] == dispatch), None)
    matches = []
    for record in state.get("stages", []):
        marker = record.get("replay") or {}
        row = next((row for row in tape.document["dispatches"] if row["id"] == marker.get("dispatch_id")), None)
        stage = row.get("original_stage") if row and row.get("report_only") is True else (row or {}).get("stage")
        record_stage = record.get("original_stage") if record.get("report_only") is True else record.get("stage")
        if (
            row is None
            or target is None
            or marker.get("manifest_sha256") != tape.sha256
            or type(marker.get("ordinal")) is not int
            or marker["ordinal"] != row["ordinal"]
            or row["ordinal"] >= target["ordinal"]
            or stage != original["stage"]
            or record_stage != stage
            or record.get("stage") != row["stage"]
            or (row.get("report_only") is True and row["stage"] != stage + "_report_repair")
            or type(record.get("exit_code")) is not int
            or record.get("exit_code") != row["exit_code"]
            or record.get("output") != actual["output"]
            or record.get("events") != actual["events"]
        ):
            continue
        for field in ("events", "output"):
            identity = row["events" if field == "events" else "report"]
            artifact = tape.document["artifacts"].get(identity) or {}
            if (
                artifact.get("redacted")
                or artifact.get("original_sha256", artifact.get("sha256")) != original[field + "_sha256"]
            ):
                break
            tape.artifact(identity)  # Reauthenticate portable bytes; never open an original path.
        else:
            matches.append(row)
    if len(matches) != 1:
        raise ValueError("Replay diverged: source-trace review occurrence is missing or ambiguous")
    source_trace.verify(state, current, obligations.sources(state), ready=True)
    archived_report = json.loads(tape.artifact(matches[0]["report"]))
    if not isinstance(archived_report, dict) or _trace(archived_report) != _trace(
        util.read_object(Path(actual["output"]))
    ):
        raise ValueError("Replay diverged: source-trace review judgment changed")
    old["review"] = copy.deepcopy(actual)
    return result
