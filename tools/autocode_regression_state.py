"""Lossless domain slice for ``state['regression_baseline']`` (#3468).

Pure bottom-layer codec: imports nothing from AutoCode at runtime, preserves
absent keys, ``None``, corrupt non-dict values, insertion order, and unknown
nested keys round-trip for saved v3 ``state.json`` compatibility and replay.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState


@dataclass(frozen=True)
class BaselineStateSlice:
    """Lossless frozen codec for ``state['regression_baseline']``."""

    present: bool = False
    is_dict: bool = False
    command: Any = None
    health: Any = None
    path: Any = None
    sha256: Any = None
    binding: Mapping[str, Any] | None = None
    whole: Mapping[str, Any] | None = None
    settled_timeouts: Mapping[str, Any] | None = None
    ordered_items: tuple[tuple[str, Any], ...] = ()
    non_dict_raw: Any = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> BaselineStateSlice:
        copied = {str(key): copy.deepcopy(value) for key, value in record.items()}
        binding = copied.get("binding")
        whole = copied.get("whole")
        settled = copied.get("settled_timeouts")
        return cls(
            present=True,
            is_dict=True,
            command=copied.get("command"),
            health=copied.get("health"),
            path=copied.get("path"),
            sha256=copied.get("sha256"),
            binding=binding if isinstance(binding, Mapping) else None,
            whole=whole if isinstance(whole, Mapping) else None,
            settled_timeouts=settled if isinstance(settled, Mapping) else None,
            ordered_items=tuple(copied.items()),
            non_dict_raw=None,
        )

    @classmethod
    def from_state(cls, state: Mapping[str, Any] | RunState | Any) -> BaselineStateSlice:
        if not isinstance(state, Mapping):
            return cls()
        mapping = cast("Mapping[str, Any]", state)
        if "regression_baseline" not in mapping:
            return cls()
        raw = mapping["regression_baseline"]
        if not isinstance(raw, dict):
            return cls(present=True, is_dict=False, non_dict_raw=copy.deepcopy(raw))
        return cls.from_record(raw)

    def to_state_value(self) -> Any:
        if not self.present:
            return None
        if not self.is_dict:
            return copy.deepcopy(self.non_dict_raw)
        return {key: copy.deepcopy(value) for key, value in self.ordered_items}

    def mapping(self) -> dict[str, Any]:
        if not self.is_dict:
            return {}
        return {key: copy.deepcopy(value) for key, value in self.ordered_items}

    def is_optional_dict(self) -> bool:
        return (not self.present) or self.is_dict or self.non_dict_raw is None

    def has_value(self) -> bool:
        return self.present and (self.is_dict or self.non_dict_raw is not None)

    def with_settled_timeouts(self, timeouts: Mapping[str, Any]) -> BaselineStateSlice:
        merged = self.mapping()
        merged["settled_timeouts"] = copy.deepcopy(dict(timeouts))
        return BaselineStateSlice.from_record(merged)

    def with_whole(self, whole_record: Mapping[str, Any]) -> BaselineStateSlice:
        merged = self.mapping()
        merged["whole"] = copy.deepcopy(dict(whole_record))
        return BaselineStateSlice.from_record(merged)

    def with_record(self, record: Mapping[str, Any], *, preserve_settled_timeouts: bool = False) -> BaselineStateSlice:
        updated = {str(key): copy.deepcopy(value) for key, value in record.items()}
        if preserve_settled_timeouts:
            existing = self.settled_timeouts if self.is_dict else None
            if existing:
                updated["settled_timeouts"] = copy.deepcopy(dict(existing))
        return BaselineStateSlice.from_record(updated)

    def without_value(self) -> BaselineStateSlice:
        return replace(
            BaselineStateSlice(),
            present=False,
        )

    def write_to_state(self, state: MutableMapping[str, Any] | RunState) -> None:
        target = cast("MutableMapping[str, Any]", state)
        if self.present:
            target["regression_baseline"] = self.to_state_value()
        else:
            target.pop("regression_baseline", None)


def mapping(state: Mapping[str, Any] | RunState | Any) -> dict[str, Any]:
    """Return ``state['regression_baseline']`` as a dict copy, or ``{}`` when absent/non-dict."""
    return BaselineStateSlice.from_state(state).mapping()


def is_optional_dict(state: Mapping[str, Any] | RunState | Any) -> bool:
    """True when ``regression_baseline`` is absent, None, or a dict."""
    return BaselineStateSlice.from_state(state).is_optional_dict()


def has_value(state: Mapping[str, Any] | RunState | Any) -> bool:
    """True when ``regression_baseline`` is present and non-None."""
    return BaselineStateSlice.from_state(state).has_value()


def write_record(
    state: MutableMapping[str, Any] | RunState,
    record: Mapping[str, Any],
    *,
    preserve_settled_timeouts: bool = False,
) -> None:
    """Replace ``state['regression_baseline']`` via ``BaselineStateSlice``."""
    BaselineStateSlice.from_state(state).with_record(
        record, preserve_settled_timeouts=preserve_settled_timeouts
    ).write_to_state(state)


def write_whole(state: MutableMapping[str, Any] | RunState, whole_record: Mapping[str, Any]) -> None:
    """Attach ``whole`` to ``state['regression_baseline']`` via ``BaselineStateSlice``."""
    BaselineStateSlice.from_state(state).with_whole(whole_record).write_to_state(state)


def write_settled_timeouts(
    state: MutableMapping[str, Any] | RunState,
    timeouts: Mapping[str, Any],
    *,
    base_record: Mapping[str, Any] | None = None,
) -> None:
    """Update ``settled_timeouts`` on ``state['regression_baseline']`` via ``BaselineStateSlice``."""
    base = BaselineStateSlice.from_record(base_record) if base_record else BaselineStateSlice.from_state(state)
    base.with_settled_timeouts(timeouts).write_to_state(state)
