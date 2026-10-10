"""Recognize the retained bound that stopped report repair; never grant an attempt.

A serialization correction counts toward the failure streak but not toward the
full repair limit. A repeated failure can therefore stop repair before that
limit. Callers still authenticate the selected attempt, source and evidence,
and require an explicit retry before starting fresh validation.

A retried attempt that fails the same way extends the same streak, so the bound
stops it before any correction or repair runs. That fresh attempt is then the
rejected attempt a further explicit retry names, never an earlier repair.
"""

try:
    from . import autocode_failures as failures
except ImportError:
    import autocode_failures as failures


RETRYABLE_ERRORS = (
    "OpenCode final message is not a JSON report; inspect the saved raw events",
    "Check is not supported by an exact executed Validator event",
)


def rejected_attempt(state):
    """The latest rejected attempt of the pending repair, or None: its latest report-only repair,
    or the original itself when no correction or repair followed it and it is still the last
    attempt of its failure ledger entry (bounded_failure decides whether the bound stopped it)."""
    pending = state.get("pending_report_repair") or {}
    if pending.get("latest_rejected"):
        return pending["latest_rejected"]
    original = pending.get("original") or {}
    entry = (state.get("failure_history") or {}).get(original.get("failure_key")) or {}
    if (
        pending.get("attempts") == 0
        and not pending.get("correction_attempted")
        and original.get("rejected")
        and original.get("failure_attempt")
        and (entry.get("attempts") or [None])[-1] == original["failure_attempt"]
    ):
        return original
    return None


def bounded_failure(state, limit):
    """Whether this pending repair reached its full-repair or exact failure bound."""
    pending = state.get("pending_report_repair") or {}
    used = pending.get("attempts")
    if type(limit) is not int or not 1 <= limit <= 2 or type(used) is not int or not 0 <= used <= limit:
        return False
    if used == limit:
        return True
    original = pending.get("original") or {}
    rejected = rejected_attempt(state) or {}
    key = original.get("failure_key")
    entry = (state.get("failure_history") or {}).get(key) or {}
    identity = entry.get("identity") or {}
    return bool(
        state.get("status") == "PAUSED_REPEATED_FAILURE"
        and key
        and rejected.get("failure_key") == key
        and failures.key(identity) == key
        and identity.get("stage") == original.get("stage")
        and identity.get("artifact_hash") == original.get("source_revision")
        and failures.stalled(entry)
    )
