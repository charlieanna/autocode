"""Recognize the retained bound that stopped report repair; never grant an attempt.

A serialization correction counts toward the failure streak but not toward the
full repair limit. A repeated failure can therefore stop repair before that
limit. Callers still authenticate the selected attempt, source and evidence,
and require an explicit retry before starting fresh validation.
"""
try:
    from . import autocode_failures as failures
except ImportError:
    import autocode_failures as failures


RETRYABLE_ERRORS = (
    'OpenCode final message is not a JSON report; inspect the saved raw events',
    'Check is not supported by an exact executed Validator event',
)


def bounded_failure(state, limit):
    """Whether this pending repair reached its full-repair or exact failure bound."""
    pending = state.get('pending_report_repair') or {}
    used = pending.get('attempts')
    if type(limit) is not int or not 1 <= limit <= 2 or type(used) is not int or not 0 <= used <= limit:
        return False
    if used == limit:
        return True
    original = pending.get('original') or {}
    rejected = pending.get('latest_rejected') or {}
    key = original.get('failure_key')
    entry = (state.get('failure_history') or {}).get(key) or {}
    identity = entry.get('identity') or {}
    return bool(state.get('status') == 'PAUSED_REPEATED_FAILURE' and key
                and rejected.get('failure_key') == key and failures.key(identity) == key
                and identity.get('stage') == original.get('stage')
                and identity.get('artifact_hash') == original.get('source_revision')
                and failures.stalled(entry))
