"""Person-facing report for a provider startup that exhausted bounded recovery.

This is a read-only presentation of authenticated saved facts. It grants no
retry, changes no route or limit, and leaves the operator's CLI controls intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_pause_authority as pause_authority
    from . import autocode_resolver_human as resolver_human
except ImportError:
    import autocode_pause_authority as pause_authority
    import autocode_resolver_human as resolver_human


def presentation(state: RunState):
    request = state.get(resolver_human.PUBLIC) or {}
    if (
        state.get("status") != "WAITING_FOR_USER"
        or not isinstance(request, dict)
        or request.get("scope") != "operational_exhaustion"
    ):
        return None
    origin = pause_authority.held_origin(state)
    if origin.get("pause_status") != "PAUSED_TIMEOUT_RECOVERY":
        return None
    attempts = state.get("automatic_timeout_recoveries") or []
    if not isinstance(attempts, list) or len(attempts) < 3:
        return None
    stage = origin.get("stage")
    if not stage or any(
        not isinstance(row, dict)
        or row.get("stage") != stage
        or row.get("timeout_kind") != "idle"
        or row.get("stall_phase") != "before_first_event"
        or row.get("completed_tool_calls") != 0
        for row in attempts[-3:]
    ):
        return None
    retained = resolver_human.retained(state)
    if retained is None:
        return None
    entry = (state.get("resolver") or {}).get("human_escalations", {}).get(retained["request_id"], {})
    binding = (entry.get("identity") or {}).get("binding") or {}
    if (binding.get("accounting") or {}).get("automatic_timeout_recoveries") != attempts:
        return None
    return {
        "category": "blocked",
        "title": "AutoCode could not reach the model provider",
        "what_happened": "The model provider did not start responding after several attempts. AutoCode could not finish this task; saved work and evidence are kept.",
        "what_it_means": "The task is not complete. AutoCode retained the failed attempts and will not repeat them without a safe recovery.",
    }
