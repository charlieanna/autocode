"""Retire a recovery note once it no longer belongs to the work about to run.

``recovery_context`` is the note the next stage reads (``autocode_stage_context``)
and the text a recovery-budget pause can quote (``autocode_recovery_limits.stop_reason``).
An abandoned attempt writes it. Two later events retire it:

- a different attempt saves successfully (``autocode.save_record``)
- a contract revision is approved (``autocode_goal_lifecycle._approve``)

A note that names no attempt, or names the attempt that just saved, stays.
Timeout and permission guards still read those until a different attempt saves
or a revision is approved.

Retired notes are appended to ``recovery_context_archive`` (a list of
``{reason, recovery_context}``). Nothing reads that archive. It keeps the old
instruction out of later prompts and pause text without dropping the record.

The budget pause names the latest counted recovery (a timeout reason or a
provider-startup failure), or says the cause is unknown. An abandon instruction
is not a counted recovery.

This module imports nothing from AutoCode.
"""

from __future__ import annotations

from pathlib import Path

UNKNOWN_CAUSE = "the cause is unknown"
STARTUP_CAUSE = "Provider startup failed before a turn; the local database was locked"
_TIMEOUT_EVENT = "automatic_timeout_recovery"
_STARTUP_EVENT = "automatic_provider_startup_recovery"
_COUNTED = frozenset({_TIMEOUT_EVENT, _STARTUP_EVENT})


def _attempt_id(record) -> str | None:
    """The same id ``autocode_run_records.attempt_id`` writes. Passed in, not imported."""
    if not isinstance(record, dict):
        return None
    iteration = record.get("iteration")
    output = record.get("output")
    if type(iteration) is not int or not output:
        return None
    return f"{iteration:03d}/{Path(output).stem}"


def _archive(state, reason: str) -> None:
    context = state.pop("recovery_context", None)
    if not context:
        return
    # recovery_context_archive: retained history of notes that must not stay current.
    # Written only here. No reader; prompts and stop_reason use recovery_context.
    state.setdefault("recovery_context_archive", []).append({"reason": reason, "recovery_context": context})


def stage_saved(state, record) -> None:
    """Retire ``recovery_context`` when the saved attempt is a different one."""
    context = state.get("recovery_context")
    if not isinstance(context, dict) or not context:
        return
    named = context.get("attempt_id")
    saved = _attempt_id(record)
    if not named or not saved or named == saved:
        return
    _archive(state, "A later stage saved")


def revision_approved(state) -> None:
    """An approved contract revision retires whatever recovery note was current."""
    if not state.get("recovery_context"):
        return
    _archive(state, "A new contract revision was approved")


def _timeout_reason(state, attempt_id) -> str | None:
    history = state.get("automatic_timeout_recoveries") or []
    for recovery in reversed(history):
        if not isinstance(recovery, dict):
            continue
        if attempt_id and recovery.get("attempt_id") not in (None, attempt_id):
            continue
        reason = recovery.get("timeout_reason")
        if isinstance(reason, str) and reason:
            return reason
    return None


def exhaustion_cause(state) -> str:
    """The recovery that spent the automatic-recovery budget, or an unknown cause.

    ``stop_reason`` quotes this as ``Last cause``. An abandoned attempt's
    instruction is never returned.
    """
    events = [
        event for event in state.get("user_events") or [] if isinstance(event, dict) and event.get("kind") in _COUNTED
    ]
    if events:
        last = events[-1]
        if last.get("kind") == _STARTUP_EVENT:
            return STARTUP_CAUSE
        reason = _timeout_reason(state, last.get("attempt_id"))
        if reason:
            return reason
    context = state.get("recovery_context") or {}
    if isinstance(context, dict):
        reason = context.get("timeout_reason")
        if isinstance(reason, str) and reason:
            return reason
    reason = _timeout_reason(state, None)
    return reason or UNKNOWN_CAUSE
