"""Display observed stage activity without settling an unfinished attempt.

A prepared slot and its timestamp precede process launch. Recorded process
identity or settled legacy evidence establishes a launch; only the caller's
fresh, classified supervision answer establishes owned work now. Neither grants
authority or changes unresolved accounting when a provider is dead or unowned.
"""

from __future__ import annotations

try:
    from . import autocode_attempt_ledger as attempt_ledger
except ImportError:
    import autocode_attempt_ledger as attempt_ledger


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _provider_pid(value):
    pid = _mapping(_mapping(value).get("provider")).get("pid")
    return type(pid) is int and pid > 0


def launched(record, liveness=None) -> bool:
    """Existing observations of launch, never a stage choice or start timestamp."""
    if not isinstance(record, dict) or not record:
        return False
    pid = record.get("pid")
    return (
        (type(pid) is int and pid > 0)
        or _provider_pid(record.get("supervision"))
        or bool(record.get("finished_at"))
        or record.get("accounted") is True
        or _provider_pid(_mapping(liveness).get("receipt"))
    )


def working_stage(state, liveness=None):
    """The open attempt whose current classified observation confirms activity."""
    record = attempt_ledger.running_stage(state)
    if launched(record, liveness) and _mapping(liveness).get("kind") == "supervised":
        return record
    return None
