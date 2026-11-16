"""First useful interaction times, recorded only by runner-owned boundaries.

The CLI records launch before workspace setup. A shown question or approval-ready
plan records its first display; Builder records a successful provider launch or
parallel Builder worker launch. Status reads only project these saved facts.
Legacy runs have no launch record, so later interactions never invent timings.
Durations are wall time and include time waiting for human input between events.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import InteractionTiming as InteractionTiming
    from .autocode_run_state import RunState
else:
    try:
        from .autocode_run_state import InteractionTiming as InteractionTiming
    except ImportError:
        from autocode_run_state import InteractionTiming as InteractionTiming

EVENTS = ("question", "plan", "builder")
KNOWN_FIELDS = ("launched_at", "first_question_at", "first_plan_at", "first_builder_at")


@dataclass(frozen=True)
class TimingSlice:
    """Lossless frozen codec for ``state['interaction_timing']`` (#3468)."""

    present: bool = False
    is_dict: bool = False
    launched_at: str | None = None
    first_question_at: str | None = None
    first_plan_at: str | None = None
    first_builder_at: str | None = None
    present_keys: frozenset[str] = frozenset()
    extra: tuple[tuple[str, Any], ...] = ()
    non_dict_raw: Any = None

    @classmethod
    def new(cls, launched_at: str) -> TimingSlice:
        return cls(
            present=True,
            is_dict=True,
            launched_at=launched_at,
            first_question_at=None,
            first_plan_at=None,
            first_builder_at=None,
            present_keys=frozenset(KNOWN_FIELDS),
        )

    @classmethod
    def from_state(cls, state: Mapping[str, Any] | RunState) -> TimingSlice:
        if not isinstance(state, Mapping):
            return cls()
        mapping = cast("Mapping[str, Any]", state)
        if "interaction_timing" not in mapping:
            return cls()
        raw = mapping["interaction_timing"]
        if not isinstance(raw, dict):
            return cls(present=True, is_dict=False, non_dict_raw=copy.deepcopy(raw))
        known_set = set(KNOWN_FIELDS)
        extra = tuple((k, copy.deepcopy(v)) for k, v in raw.items() if k not in known_set)
        return cls(
            present=True,
            is_dict=True,
            launched_at=copy.deepcopy(raw.get("launched_at")),
            first_question_at=copy.deepcopy(raw.get("first_question_at")),
            first_plan_at=copy.deepcopy(raw.get("first_plan_at")),
            first_builder_at=copy.deepcopy(raw.get("first_builder_at")),
            present_keys=frozenset(k for k in KNOWN_FIELDS if k in raw),
            extra=extra,
        )

    def to_state_value(self) -> Any:
        if not self.present:
            return None
        if not self.is_dict:
            return copy.deepcopy(self.non_dict_raw)
        result: dict[str, Any] = {}
        if "launched_at" in self.present_keys:
            result["launched_at"] = copy.deepcopy(self.launched_at)
        if "first_question_at" in self.present_keys:
            result["first_question_at"] = copy.deepcopy(self.first_question_at)
        if "first_plan_at" in self.present_keys:
            result["first_plan_at"] = copy.deepcopy(self.first_plan_at)
        if "first_builder_at" in self.present_keys:
            result["first_builder_at"] = copy.deepcopy(self.first_builder_at)
        for key, value in self.extra:
            result[key] = copy.deepcopy(value)
        return result

    def write_to_state(self, state: MutableMapping[str, Any] | RunState) -> None:
        target = cast("MutableMapping[str, Any]", state)
        if self.present:
            target["interaction_timing"] = self.to_state_value()
        else:
            target.pop("interaction_timing", None)

    def with_event(self, event: str, at: str) -> TimingSlice | None:
        if event not in EVENTS:
            raise ValueError("Unknown interaction timing event")
        if not self.is_dict or not self.launched_at:
            return None
        if event == "question":
            if self.first_question_at is not None:
                return None
            return replace(self, first_question_at=at, present_keys=self.present_keys | {"first_question_at"})
        if event == "plan":
            if self.first_plan_at is not None:
                return None
            return replace(self, first_plan_at=at, present_keys=self.present_keys | {"first_plan_at"})
        if self.first_builder_at is not None:
            return None
        return replace(self, first_builder_at=at, present_keys=self.present_keys | {"first_builder_at"})


def begin(state: RunState, launched_at: str) -> None:
    """Called only when creating a new run; resumes keep the original record."""
    TimingSlice.new(launched_at).write_to_state(state)


def mark(state: RunState, event: str, at: str) -> bool:
    """Latch a first observed event; legacy state and repeated displays stay unchanged."""
    if event not in EVENTS:
        raise ValueError("Unknown interaction timing event")
    current = TimingSlice.from_state(state)
    updated = current.with_event(event, at)
    if updated is None:
        return False
    updated.write_to_state(state)
    return True


def has_record(state: RunState) -> bool:
    """Whether ``state`` holds a truthy ``interaction_timing`` record."""
    timing = TimingSlice.from_state(state)
    return bool(timing.present and timing.to_state_value())


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
    timing = TimingSlice.from_state(state)
    record: Mapping[str, object] = timing.to_state_value() if timing.is_dict else {}
    result: dict[str, object] = {"launched_at": record.get("launched_at")}
    for event in EVENTS:
        key = f"first_{event}_at"
        result[key] = record.get(key)
        result[f"first_{event}_seconds"] = elapsed(result["launched_at"], result[key])
    return result
