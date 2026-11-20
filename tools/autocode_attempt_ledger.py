"""The recorded settled attempts shared by planning limits and usage readers.

An active slot is prepared before provider exec. Only its recorded finish or
completed accounting makes it a settled attempt; PID, status and role do not.
History mirrors are not another source of attempts. One artifact can have
several observations, including conflicting snapshots or a runner audit record.
Grouping identifies a call; it never chooses which observation is true.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple

# The core records why it set a cleanly stopped attempt's result aside (read-only source drift).
UNUSABLE = "unusable_reason"
REASON_LIMIT = 500


def running_stage(state: Mapping[str, Any]):
    """The in-flight record, excluding a stopped attempt kept for reconciliation."""
    record = state.get("active_stage")
    if not isinstance(record, dict):
        return None
    return record if record and not record.get("finished_at") and record.get("accounted") is not True else None


def attempt_identity(record, *, namespace="", index=None):
    """Artifact identity survives parent imports; anonymous legacy rows never merge."""
    for field in ("events", "output"):
        value = record.get(field)
        if isinstance(value, str) and value:
            path = Path(value)
            if path.is_absolute():
                return f"{field}:{path.resolve()}"
            if namespace:
                return f"{field}:{(Path(namespace) / path).resolve()}"
    for field in ("worker_attempt", "attempt_id"):
        if isinstance(record.get(field), str) and record[field]:
            return f"{namespace}:{field}:{record[field]}"
    return f"{namespace}:unidentified:{index}"


def run_identity(value):
    """Canonicalize public run paths without opening any run state."""
    if not value:
        return None
    path = Path(str(value))
    return str(path.resolve()) if path.is_absolute() else str(value)


def _artifact_path(value, namespace):
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return str(path.resolve()) if path.is_absolute() else str((Path(namespace) / path).resolve()) if namespace else None


def _archive_aliases(records, namespace):
    aliases, ambiguous = {}, set()
    for record in records:
        archived = record.get("archived_paths")
        if not isinstance(archived, dict):
            continue
        for old, new in archived.items():
            old, new = _artifact_path(old, namespace), _artifact_path(new, namespace)
            if old is None or new is None or old == new:
                continue
            if old in aliases and aliases[old] != new:
                ambiguous.add(old)
            aliases[old] = new
    return {old: new for old, new in aliases.items() if old not in ambiguous}


def _canonical(identity, aliases):
    field, separator, path = identity.partition(":")
    if field not in ("events", "output"):
        return identity
    seen = set()
    while path in aliases:
        if path in seen:
            return identity  # A cyclic archive does not establish equivalence.
        seen.add(path)
        path = aliases[path]
    return field + separator + path


def observations(state: Mapping[str, Any], *, include_running=False):
    """(canonical identity, raw observation), preserving conflicting evidence."""
    stages = state.get("stages")
    records = [row for row in stages if isinstance(row, dict)] if isinstance(stages, list) else []
    retained = state.get("active_stage")
    if (
        isinstance(retained, dict)
        and retained
        and (include_running or not running_stage(state))
        and not any(row is retained for row in records)
    ):
        records.append(retained)
    namespace = run_identity(state.get("run_dir") or state.get("task_id")) or ""
    aliases = _archive_aliases(records, namespace)
    return [
        (_canonical(attempt_identity(record, namespace=namespace, index=index), aliases), record)
        for index, record in enumerate(records)
    ]


def groups(state: Mapping[str, Any]):
    """All settled observations per identity; no metric or provenance is erased."""
    grouped: dict[Any, Any] = {}
    for identity, record in observations(state):
        grouped.setdefault(identity, []).append(record)
    return grouped.values()


def finished_records(state: Mapping[str, Any]):
    """Legacy saved-observation projection, plus an otherwise unrepresented retained attempt.

    Saved rows and raw artifact identity retain the legacy known-spend contract.
    Archive equivalence belongs to observations(), not this subtotal projection.
    Nullable all-attempt accounting reads all observations before resolving
    copies or conflicts, rather than treating this projection as a billing ledger.
    """
    stages = state.get("stages")
    records = [item for item in stages if isinstance(item, dict)] if isinstance(stages, list) else []
    retained = state.get("active_stage")
    if isinstance(retained, dict) and retained and not running_stage(state):
        namespace = run_identity(state.get("run_dir") or state.get("task_id")) or ""
        identity = attempt_identity(retained, namespace=namespace, index=len(records))
        if not any(
            record is retained or attempt_identity(record, namespace=namespace, index=index) == identity
            for index, record in enumerate(records)
        ):
            records.append(retained)
    return records


def mark_unusable(record, reason):
    """Record on a stopped attempt why its result is not used; the journal reads it as its outcome."""
    record[UNUSABLE] = str(reason)[:REASON_LIMIT]


class JournalAttempt(NamedTuple):
    """One attempt as the activity journal sees it; ``outcome`` is None until it is terminal."""

    digest: str
    index: int | None  # position in stages, None for the retained active stage
    record: dict
    outcome: str | None
    reason: str | None
    former: tuple[str, ...] = ()  # digests of the same attempt before its artifacts were archived


def _short(identity):
    # The journal records no file names: an attempt is named by a short digest of its identity.
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def _digest(record, namespace, index):
    return _short(attempt_identity(record, namespace=namespace, index=index))


def _former(record, namespace):
    """Digests of the identity this attempt had before archive_rejected_stage moved its artifacts."""
    archived = record.get("archived_paths")
    if not isinstance(archived, dict):
        return ()
    for field in ("events", "output"):
        current = _artifact_path(record.get(field), namespace)
        if current is not None:
            return tuple(
                _short(f"{field}:{_artifact_path(old, namespace)}")
                for old, new in archived.items()
                if _artifact_path(old, namespace) is not None and _artifact_path(new, namespace) == current
            )
    return ()


def _saved_outcome(record):
    """A saved row is terminal unless it still carries the preflight admission marker."""
    if record.get("preflight_complete") is False:
        return None  # admitted, or finished with provider cleanup unverified: never inferred settled
    preflight = record.get("preflight_outcome")
    if isinstance(preflight, dict) and isinstance(preflight.get("status"), str) and preflight["status"]:
        return preflight["status"]
    return "rejected" if record.get("rejected") else "completed"


def _retained_outcome(state, record):
    """A retained attempt is terminal only once the core rejected its result and the run stopped."""
    reason = record.get(UNUSABLE)
    finished = record.get("finished_at") or record.get("completed_at")
    if isinstance(reason, str) and reason and finished and state.get("status") != "RUNNING":
        return "unaccepted", reason[:REASON_LIMIT]
    return None, None


def journal(state: Mapping[str, Any]):
    """Every attempt with its terminal outcome for the activity journal, in saved order.

    Saved rows come first; a stopped retained active stage that no saved row represents
    comes last. The journal announces an attempt once, when its outcome is first known.
    """
    stages = state.get("stages")
    stages = stages if isinstance(stages, list) else []
    namespace = run_identity(state.get("run_dir") or state.get("task_id")) or ""
    found = [
        JournalAttempt(_digest(row, namespace, index), index, row, _saved_outcome(row), None, _former(row, namespace))
        for index, row in enumerate(stages)
        if isinstance(row, dict)
    ]
    retained = state.get("active_stage")
    if isinstance(retained, dict) and retained and not running_stage(state):
        digest = _digest(retained, namespace, len(stages))
        if not any(row.record is retained or digest in (row.digest, *row.former) for row in found):
            outcome, reason = _retained_outcome(state, retained)
            found.append(JournalAttempt(digest, None, retained, outcome, reason))
    return found
