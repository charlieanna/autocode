"""A run's saved status in a few words a person reads, for lists of chats.

The registry list and ``runs --json`` carry a run's raw ``status`` (``PAUSED_PLANNING_BUDGET``),
which a client must not show. This is the short label they show instead: whether the run is
working, finished, waiting for the person, or paused and whose move the pause is, using the one
pause-cause table (``autocode_pause_category``). Pure: it reads a status string and nothing else,
so a list can label every row without assembling a status view for each.
"""

from __future__ import annotations

try:
    from . import autocode_pause_category as pause_category
except ImportError:
    import autocode_pause_category as pause_category

FIXED = {
    "RUNNING": "Working",
    "DISCOVERING": "Working",
    "PREPARING": "Working",
    "COMPLETE": "Finished",
    "TASK_COMPLETE": "Finished",
    "AWAITING_GOAL_APPROVAL": "Plan ready for your approval",
    "WAITING_FOR_USER": "Waiting for your answer",
    "WAITING_FOR_DEPENDENCY": "Waiting for another chat",
    "PAUSED_REQUESTED": "Paused at your request",
    "BLOCKED_HUMAN": "Needs your decision",
    "RESOLVER_PENDING": "AutoCode is resolving a stop",
}
# Whose move a pause is, as the pause-cause table names it.
PAUSED = {
    "your_decision": "Paused for your decision",
    "your_environment": "Paused: check your environment",
    "model_or_provider": "Paused: model or provider problem",
    "autocode": "Paused by AutoCode",
}


def label(status: object) -> str | None:
    """The person's words for ``status``; None when there is no status to describe."""
    if not isinstance(status, str) or not status.strip():
        return None
    if status in FIXED:
        return FIXED[status]
    cause = pause_category.category(status)
    if cause in PAUSED:
        return PAUSED[cause]
    # A status no table names yet reads as words, never as the raw code.
    return status.replace("_", " ").strip().lower().capitalize()
