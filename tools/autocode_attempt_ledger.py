"""The recorded settled attempts shared by planning limits and usage readers.

An active slot is prepared before provider exec. Only its recorded finish or
completed accounting makes it a settled attempt; PID, status and role do not.
History mirrors are not another source of attempts.
"""

from __future__ import annotations

from pathlib import Path


def running_stage(state):
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


def finished_records(state):
    """Saved attempts and a settled retained attempt, once by recorded identity."""
    stages = state.get("stages")
    records = [row for row in stages if isinstance(row, dict)] if isinstance(stages, list) else []
    retained = state.get("active_stage")
    if (
        isinstance(retained, dict)
        and retained
        and not running_stage(state)
        and not any(row is retained for row in records)
    ):
        records.append(retained)
    namespace = run_identity(state.get("run_dir") or state.get("task_id")) or ""
    aliases = _archive_aliases(records, namespace)
    selected, positions = [], {}
    for index, record in enumerate(records):
        identity = attempt_identity(record, namespace=namespace, index=index)
        key = _canonical(identity, aliases)
        if key not in positions:
            positions[key] = len(selected)
            selected.append(record)
        elif key != identity:
            continue
        else:
            # Prefer the kept artifact over its explicitly recorded old path.
            position = positions[key]
            previous = attempt_identity(selected[position], namespace=namespace, index=position)
            if previous != key:
                selected[position] = record
    return selected
