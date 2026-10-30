"""The first owner of run-status transitions.

Imports nothing from AutoCode, so any layer can transition a run without joining an import cycle.
``tests/test_run_status.py`` fails when another module writes the paused phase itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

PAUSE_PHASE = "PAUSED_OR_BLOCKED"
# The pauses that take brief feedback as plan feedback: the Tester's names --feedback as its way to revise the plan (#1869).
PLAN_FEEDBACK_PAUSES = ("PAUSED_PLANNING_BUDGET", "PAUSED_COMPONENT_PLAN", "PAUSED_ACCEPTANCE_TESTS")
_RESERVED = frozenset({"status", "phase", "stop_reason"})


def _check_fields(fields: dict) -> None:
    # Transition kwargs remain dynamic boundary inputs; this guard prevents reserved-key overrides.
    duplicate = _RESERVED.intersection(fields)
    if duplicate:
        raise TypeError("status transition fields are reserved: " + ", ".join(sorted(duplicate)))


def pause(state: RunState, status: str, reason: str | None = None, **fields) -> RunState:
    """Pause ``state`` with ``status``; returns it.

    ``reason`` replaces the stop reason; None keeps the one the run already has. ``fields`` are
    the other keys this pause sets with it, such as ``next_stage`` or ``paused_at``.
    """
    _check_fields(fields)
    state.update({"status": status, "phase": PAUSE_PHASE, **cast("RunState", fields)})
    if reason is not None:
        state["stop_reason"] = reason
    return state


def running(state: RunState, phase: str, **fields) -> RunState:
    """Continue ``state`` in ``phase`` and retire its previous stop reason; returns it."""
    _check_fields(fields)
    state.update({"status": "RUNNING", "phase": phase, **cast("RunState", fields)})
    state.pop("stop_reason", None)
    return state


def awaiting_approval(state: RunState, **fields) -> RunState:
    """Return to human plan approval without granting approval or execution."""
    _check_fields(fields)
    state.update({"status": "AWAITING_GOAL_APPROVAL", "phase": "AWAITING_GOAL_APPROVAL", **cast("RunState", fields)})
    state.pop("stop_reason", None)
    return state


# How the stop reason of a run AutoCode ended itself begins (autocode_stop.end, #2485). Named here, the
# leaf module, so the stop policy, the explanation and the pause category read one value.
ENDED_PREFIX = "AutoCode could not finish this task: "
