"""Prove a legacy recovery streak from the retained, balanced event ledger."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

COUNTED = frozenset(
    {
        "automatic_timeout_recovery",
        "automatic_provider_startup_recovery",
        "automatic_provider_network_recovery",
    }
)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def saved_checkpoint(record):
    return (
        isinstance(record, dict)
        and type(record.get("exit_code")) is int
        and record["exit_code"] == 0
        and not any(record.get(key) for key in ("runner_owned", "rejected", "abandoned", "timed_out", "interrupted"))
        and all(record.get(key) for key in ("stage", "output", "events", "schema"))
        and timestamp(record.get("finished_at")) is not None
    )


def legacy_progress(state: RunState, raw):
    """Return progress credit only when the whole old ledger balances exactly.

    Unknown recoveries, missing/duplicate events, invalid dates and inconsistent
    grants retain the old bound. Reads never change a request's bound inputs.
    """
    checkpoints = [row for row in state.get("stages", []) if saved_checkpoint(row)]
    if not checkpoints or type(raw) is not int or raw <= 0:
        return None
    checkpoint = max(checkpoints, key=lambda row: timestamp(row["finished_at"]))
    cutoff = timestamp(checkpoint["finished_at"])
    balance = streak = 0
    seen = set()
    previous = None
    had_earlier_failure = False
    for event in state.get("user_events", []):
        if not isinstance(event, dict) or event.get("kind") not in COUNTED | {"recovery_grant"}:
            continue
        at = timestamp(event.get("at"))
        if at is None or (previous is not None and at < previous):
            return None
        previous = at
        identity = (event["kind"], event.get("attempt_id") or event.get("events") or event.get("request_id"))
        if not identity[1] or identity in seen:
            return None
        seen.add(identity)
        if event["kind"] == "recovery_grant":
            amount = event.get("amount")
            if type(amount) is not int or amount < 1 or event.get("previous_count") != balance:
                return None
            balance = max(0, balance - amount)
            if at > cutoff:
                streak = max(0, streak - amount)
        else:
            balance += 1
            if at > cutoff:
                streak += 1
            else:
                had_earlier_failure = True
    if balance != raw or not had_earlier_failure or streak >= raw:
        return None
    return {
        "previous_count": raw,
        "remaining_count": streak,
        "checkpoint": {key: checkpoint[key] for key in ("stage", "output", "finished_at")},
    }
