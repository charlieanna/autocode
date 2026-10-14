"""First useful interaction times, recorded only by runner-owned boundaries.

The CLI records launch before workspace setup. A shown question or approval-ready
plan records its first display; Builder records a successful provider launch or
parallel Builder worker launch. Status reads only project these saved facts.
Legacy runs have no launch record, so later interactions never invent timings.
Durations are wall time and include time waiting for human input between events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import InteractionTiming as InteractionTiming
    from .autocode_run_state import RunState
else:
    try:
        from .autocode_run_state import InteractionTiming as InteractionTiming
    except ImportError:
        from autocode_run_state import InteractionTiming as InteractionTiming

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

EVENTS = ("question", "plan", "builder")


def begin(state: RunState, launched_at: str) -> None:
    """Called only when creating a new run; resumes keep the original record."""
    state["interaction_timing"] = {
        "launched_at": launched_at,
        "first_question_at": None,
        "first_plan_at": None,
        "first_builder_at": None,
    }


def mark(state: RunState, event: str, at: str) -> bool:
    """Latch a first observed event; legacy state and repeated displays stay unchanged."""
    if event not in EVENTS:
        raise ValueError("Unknown interaction timing event")
    record = state.get("interaction_timing")
    if not isinstance(record, dict) or not record.get("launched_at"):
        return False
    key: Literal["first_question_at", "first_plan_at", "first_builder_at"]
    if event == "question":
        key = "first_question_at"
    elif event == "plan":
        key = "first_plan_at"
    else:
        key = "first_builder_at"
    if record.get(key) is not None:
        return False
    record[key] = at
    return True


def presented(state: RunState, public: dict | None, *, at: str) -> None:
    """After successful rendering, record only the human request actually shown."""
    if not public:
        return
    if state.get("status") == "AWAITING_GOAL_APPROVAL" and public.get("scope") == "goal_approval":
        mark(state, "plan", at)
    elif state.get("status") == "WAITING_FOR_USER" and (
        public.get("questions") or (public.get("request") or {}).get("decision_needed")
    ):
        mark(state, "question", at)


def elapsed(launched_at, at) -> float | None:
    if not isinstance(launched_at, str) or not isinstance(at, str):
        return None
    try:
        launch, event = datetime.fromisoformat(launched_at), datetime.fromisoformat(at)
        if launch.tzinfo is None or event.tzinfo is None:
            return None
        seconds = (event - launch).total_seconds()
        return seconds if seconds >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def project(state: RunState) -> dict:
    """Additive public fields; missing/legacy/unreached events remain unknown."""
    raw = state.get("interaction_timing")
    record: Mapping[str, object] = raw if isinstance(raw, dict) else {}
    result: dict[str, object] = {"launched_at": record.get("launched_at")}
    for event in EVENTS:
        key = f"first_{event}_at"
        result[key] = record.get(key)
        result[f"first_{event}_seconds"] = elapsed(result["launched_at"], result[key])
    return result
