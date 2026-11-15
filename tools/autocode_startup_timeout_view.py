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


def presentation(state: RunState):
    if stop.applied_stop(state):
        return None
    return _startup(state) or disposition.presentation(state)


def _startup(state: RunState):
    held = disposition.held(state)
    if held is None:
        return None
    detail = disposition.original_detail(state, held)
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    if evidence.get("status") != "PAUSED_TIMEOUT_RECOVERY":
        return None
    attempts = state.get("automatic_timeout_recoveries") or []
    if not isinstance(attempts, list) or len(attempts) < 3:
        return None
    stage = evidence.get("stage")
    if not stage or any(
        not isinstance(row, dict)
        or row.get("stage") != stage
        or row.get("timeout_kind") != "idle"
        or row.get("stall_phase") != "before_first_event"
        or row.get("completed_tool_calls") != 0
        for row in attempts[-3:]
    ):
        return None
    if (binding.get("accounting") or {}).get("automatic_timeout_recoveries") != attempts:
        return None
    return {
        "category": "blocked",
        "title": "AutoCode could not start the model step",
        "what_happened": "The model step did not start responding after several attempts. AutoCode could not finish this task; saved work and evidence are kept.",
        "what_it_means": "The task is not complete. AutoCode retained the failed attempts and will not repeat them without a safe recovery.",
    }
