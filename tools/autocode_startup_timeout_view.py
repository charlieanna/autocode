"""Person-facing report for a provider startup that exhausted bounded recovery.

This is a read-only presentation of authenticated saved facts. It grants no
retry, changes no route or limit, and leaves the operator's CLI controls intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_operational_disposition as disposition
    from . import autocode_stop as stop
except ImportError:
    import autocode_operational_disposition as disposition
    import autocode_stop as stop


# The last cause a spent recovery allowance records when only these stalls are known.
STALLED_CAUSE = "the model step did not start responding after several attempts"


def stalled_before_start(state: RunState, stage: str | None = None) -> bool:
    """The last three automatic timeout recoveries of ``stage`` (default: the last one's) never saw a first event."""
    attempts = state.get("automatic_timeout_recoveries") or []
    if not isinstance(attempts, list) or len(attempts) < 3:
        return False
    stage = stage or (attempts[-1].get("stage") if isinstance(attempts[-1], dict) else None)
    return bool(stage) and all(
        isinstance(row, dict)
        and row.get("stage") == stage
        and row.get("timeout_kind") == "idle"
        and row.get("stall_phase") == "before_first_event"
        and row.get("completed_tool_calls") == 0
        for row in attempts[-3:]
    )


def presentation(state: RunState):
    if stop.applied_stop(state):
        return None
    return _startup(state)


def _startup(state: RunState):
    held = disposition.held(state)
    if held is None:
        return None
    detail = disposition.original_detail(state, held)
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    if evidence.get("status") != "PAUSED_TIMEOUT_RECOVERY":
        return None
    attempts = state.get("automatic_timeout_recoveries") or []
    if not stalled_before_start(state, evidence.get("stage")):
        return None
    if (binding.get("accounting") or {}).get("automatic_timeout_recoveries") != attempts:
        return None
    return {
        "category": "blocked",
        "title": "AutoCode could not start the model step",
        "what_happened": "The model step did not start responding after several attempts. AutoCode could not finish this task; saved work and evidence are kept.",
        "what_it_means": "The task is not complete. AutoCode retained the failed attempts and will not repeat them without a safe recovery.",
    }
