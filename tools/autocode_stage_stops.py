"""The typed stops the runner names from a finished stage's events: a content-filter refusal (#464) and a
context window that ran out or was compacted (#1942).

Pure over a stage record: read its event log, name the job on screen and let the classifiers
(``autocode_provider_refusal``, ``autocode_context_window``) say what the provider did. The runner
calls these where it decides how an exit ends (``autocode.run_role`` and ``reconcile_active``); nothing
here launches, retries or writes state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_context_window as context_window
    from . import autocode_provider_refusal as provider_refusal
    from . import autocode_request_usage as request_usage
    from . import autocode_status
    from . import autocode_support as support
except ImportError:
    import autocode_context_window as context_window
    import autocode_provider_refusal as provider_refusal
    import autocode_request_usage as request_usage
    import autocode_status
    import autocode_support as support


def _job(state: RunState, record) -> str:
    return autocode_status.role_name(record.get("original_stage") or record.get("stage"), state)


def _model(record):
    return (record.get("launch_route") or {}).get("model")


def refusal_reason(state: RunState, record):
    """Whose response the provider's content filter refused, and on which model; None for any other stop."""
    if not record.get("events"):
        return None
    return provider_refusal.explain(support.events(record["events"]), job=_job(state, record), model=_model(record))


def refused_at_clean_exit(state: RunState, record, role, events):
    """A content-filter refusal names the stop even when the provider exited 0 (#464); any other stop stays uncertain."""
    if refused := refusal_reason(state, record):
        raise support.Paused(
            provider_refusal.STATUS, f"{refused}. {role} exited 0; reconcile {events}, no automatic replay"
        )


def context_window_reason(state: RunState, record):
    """The stage's request no longer fit its model's context window, or the provider compacted the conversation
    (#1942), with the size of the last completed request; None for any other stop."""
    if not record.get("events"):
        return None
    try:
        measured = request_usage.read(record["events"])
    except (OSError, ValueError, TypeError):
        measured = {}
    last = measured.get("last") if isinstance(measured, dict) else None
    return context_window.explain(
        support.events(record["events"]),
        job=_job(state, record),
        model=_model(record),
        request_tokens=last.get("input_tokens") if isinstance(last, dict) else None,
    )


def stopped_at_context_window(state: RunState, record, role, events):
    """A compacted or overflowed context names the stop even when the provider exited 0: its response was
    produced from a summary, so it is refused rather than left uncertain (#1942)."""
    if reason := context_window_reason(state, record):
        raise support.Paused(
            context_window.STATUS, f"{reason}. {role} exited 0; reconcile {events}, no automatic replay"
        )
