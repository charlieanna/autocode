"""Durable identities for repeated stage failures; no provider requests."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support


REPEAT_THRESHOLD = 3
PROBE_BYTES = 262_144


def identity(record, error):
    artifact_hash = record.get("source_revision")
    if not artifact_hash:
        return None
    return {
        "stage": record.get("original_stage") or record["stage"],
        "artifact_hash": artifact_hash,
        "error_class": getattr(error, "status", None) or type(error).__name__,
    }


def key(identity_value):
    return support.digest(identity_value)


def _probe(record):
    """Inspect saved output once, without replaying a tool or provider request."""
    result = {}
    for label, field in (("provider_events", "events"), ("final_output", "output")):
        path = Path(record.get(field) or "")
        result[label] = {"present": path.is_file()}
        if not path.is_file():
            continue
        size = path.stat().st_size
        result[label]["bytes"] = size
        if size > PROBE_BYTES:
            result[label]["parse"] = "skipped_size_limit"
            continue
        try:
            if field == "output":
                result[label]["parse"] = "json_object" if isinstance(json.loads(path.read_text()), dict) else "json_other"
            else:
                rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                result[label]["parse"] = "jsonl"
                result[label]["terminal_turn"] = any(row.get("type") == "turn.completed" for row in rows if isinstance(row, dict))
        except (OSError, UnicodeError, ValueError):
            result[label]["parse"] = "invalid_or_unreadable"
    return result


def record(state, stage_record, error, at):
    """Count each completed attempt at most once in the authoritative state."""
    selected = identity(stage_record, error)
    if selected is None:
        return None
    ledger = state.setdefault("failure_history", {})
    failure_key = key(selected)
    entry = ledger.setdefault(failure_key, {"identity": selected, "count": 0, "attempts": []})
    attempt = stage_record.setdefault(
        "failure_attempt", f"{stage_record['iteration']}:{stage_record['stage']}:{stage_record['output']}")
    if attempt not in entry["attempts"]:
        entry["attempts"].append(attempt)
        entry["count"] += 1
        entry["last_seen"] = at
        entry["last_error"] = str(error)
        if entry["count"] >= REPEAT_THRESHOLD and "output_probe" not in entry:
            entry["output_probe"] = {"attempts": 1, "result": _probe(stage_record)}
    stage_record["failure_key"] = failure_key
    return entry


def repeated(state, stage_record):
    failure_key = stage_record.get("failure_key")
    entry = (state.get("failure_history") or {}).get(failure_key)
    if entry and entry.get("count", 0) >= REPEAT_THRESHOLD:
        return entry
    stage = stage_record.get("original_stage") or stage_record.get("stage")
    artifact_hash = stage_record.get("source_revision")
    for candidate in (state.get("failure_history") or {}).values():
        selected = candidate.get("identity") or {}
        if (selected.get("stage") == stage and selected.get("artifact_hash") == artifact_hash
                and candidate.get("count", 0) >= REPEAT_THRESHOLD):
            return candidate
    return None
