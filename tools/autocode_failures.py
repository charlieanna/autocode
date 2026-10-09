"""Durable identities for repeated stage failures; no provider requests.

The ledger groups failures by stage, source and error class and keeps every
attempt, so the history stays inspectable. A stage counts as stalled only when
its most recent consecutive attempts at that source failed the same way: the
same error text (see ``signature``) and the same kind of saved output. Distinct
problems that share an exception class, such as missing responses to different
concerns, are new information and never add up to a repeated-failure pause.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


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
    return util.digest(identity_value)


def _owner(row):
    return row.get("original_stage") or row.get("stage")


def signature(stage_record, error, probe):
    """What a stalled attempt repeats: its error text and the kind of output it saved.

    Only per-attempt noise is normalized (this attempt's artifact paths, long hex
    digests, whitespace). Identifiers such as concern IDs are kept.
    """
    text = str(error)
    if stage_record.get("output"):
        text = text.replace(str(Path(stage_record["output"]).with_suffix("")), "<attempt>")
    text = " ".join(re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", text).split())
    kinds = {label: {k: v for k, v in row.items() if k != "bytes"} for label, row in probe.items()}
    return util.digest({"error_class": getattr(error, "status", None) or type(error).__name__,
                           "error": text, "output": kinds})


def _interrupted(state, stage_record, entry):
    """Whether this stage had any other outcome after the entry's last failure.

    Rows appended to ``stages`` since that failure (successes, archived timeouts,
    failures with another identity) end the run. The ledger itself carries the run,
    so a caller that records a failure without appending its row loses nothing.
    """
    for row in (state.get("stages") or [])[entry.get("stage_index", 0):]:
        if (row is stage_record or row.get("runner_owned") or _owner(row) != _owner(stage_record)
                or row.get("failure_attempt") in (*entry["attempts"], stage_record["failure_attempt"])):
            continue
        return True
    return False


def stalled(entry):
    """Ledger entries saved before streaks existed keep their old cumulative meaning."""
    return entry.get("streak", entry.get("count", 0)) >= REPEAT_THRESHOLD


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
        probe = _probe(stage_record)
        mark = signature(stage_record, error, probe)
        continues = (entry.get("signature") == mark and entry.get("streak", 0) > 0
                     and not _interrupted(state, stage_record, entry))
        entry["attempts"].append(attempt)
        entry["count"] += 1
        entry.update(last_seen=at, last_error=str(error), signature=mark,
                     streak=entry["streak"] + 1 if continues else 1,
                     stage_index=len(state.get("stages") or []))
        stage_record["failure_signature"] = mark
        if stalled(entry) and "output_probe" not in entry:
            entry["output_probe"] = {"attempts": 1, "result": probe}
        # This failure ends any other run of failures of the same stage, at any source.
        for other_key, other in ledger.items():
            if other_key != failure_key and (other.get("identity") or {}).get("stage") == selected["stage"]:
                other["streak"] = 0
    stage_record["failure_key"] = failure_key
    return entry


def repeated(state, stage_record):
    failure_key = stage_record.get("failure_key")
    entry = (state.get("failure_history") or {}).get(failure_key)
    if entry and stalled(entry):
        return entry
    stage = stage_record.get("original_stage") or stage_record.get("stage")
    artifact_hash = stage_record.get("source_revision")
    for candidate in (state.get("failure_history") or {}).values():
        selected = candidate.get("identity") or {}
        if (selected.get("stage") == stage and selected.get("artifact_hash") == artifact_hash
                and stalled(candidate)):
            return candidate
    return None
