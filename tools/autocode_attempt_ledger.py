"""The recorded settled attempts shared by planning limits and usage readers.

An active slot is prepared before provider exec. Only its recorded finish or
completed accounting makes it a settled attempt; PID, status and role do not.
History mirrors are not another source of attempts. One artifact can have
several observations, including conflicting snapshots or a runner audit record.
Grouping identifies a call; it never chooses which observation is true.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


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
