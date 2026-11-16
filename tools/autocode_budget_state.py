"""Lossless domain slice for run-level budget counters (``active_seconds``, ``inherited_spend``) (#3468).

Pure bottom-layer codec: imports nothing from AutoCode at runtime, preserves
absent keys, explicit falsy/non-dict values, and unknown nested keys round-trip
for saved v3 ``state.json`` compatibility and ``autocode replay``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

_KNOWN_INHERITED_KEYS = ("usd", "unpriced_stages", "partial_stages")


@dataclass(frozen=True)
class InheritedSpendSlice:
    """Lossless frozen view of ``state['inherited_spend']`` when present as a dict."""

    usd: Any = None
    unpriced_stages: Any = None
    partial_stages: Any = None
    present_keys: frozenset[str] = frozenset()
    extra: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> InheritedSpendSlice:
        known = set(_KNOWN_INHERITED_KEYS)
        extra = tuple((str(key), copy.deepcopy(value)) for key, value in raw.items() if key not in known)
        return cls(
            usd=copy.deepcopy(raw.get("usd")),
            unpriced_stages=copy.deepcopy(raw.get("unpriced_stages")),
            partial_stages=copy.deepcopy(raw.get("partial_stages")),
            present_keys=frozenset(key for key in _KNOWN_INHERITED_KEYS if key in raw),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "usd" in self.present_keys:
            result["usd"] = copy.deepcopy(self.usd)
        if "unpriced_stages" in self.present_keys:
            result["unpriced_stages"] = copy.deepcopy(self.unpriced_stages)
        if "partial_stages" in self.present_keys:
            result["partial_stages"] = copy.deepcopy(self.partial_stages)
        for key, value in self.extra:
            result[key] = copy.deepcopy(value)
        return result


@dataclass(frozen=True)
class BudgetSlice:
    """Lossless frozen codec for ``state['active_seconds']`` and ``state['inherited_spend']``."""

    active_seconds_present: bool = False
    active_seconds_value: Any = None
    inherited_spend_present: bool = False
    inherited_spend_is_dict: bool = False
    inherited_spend: InheritedSpendSlice | None = None
    inherited_spend_non_dict: Any = None

    @classmethod
    def from_state(cls, state: Mapping[str, Any] | RunState | Any) -> BudgetSlice:
        if not isinstance(state, Mapping):
            return cls()
        mapping = cast("Mapping[str, Any]", state)
        active_present = "active_seconds" in mapping
        active_val = copy.deepcopy(mapping["active_seconds"]) if active_present else None
        inherited_present = "inherited_spend" in mapping
        inherited_raw = mapping["inherited_spend"] if inherited_present else None
        if inherited_present and isinstance(inherited_raw, dict):
            return cls(
                active_seconds_present=active_present,
                active_seconds_value=active_val,
                inherited_spend_present=True,
                inherited_spend_is_dict=True,
                inherited_spend=InheritedSpendSlice.from_mapping(inherited_raw),
            )
        return cls(
            active_seconds_present=active_present,
            active_seconds_value=active_val,
            inherited_spend_present=inherited_present,
            inherited_spend_is_dict=False,
            inherited_spend=None,
            inherited_spend_non_dict=copy.deepcopy(inherited_raw) if inherited_present else None,
        )

    def write_to_state(self, state: MutableMapping[str, Any] | RunState) -> None:
        target = cast("MutableMapping[str, Any]", state)
        if self.active_seconds_present:
            target["active_seconds"] = copy.deepcopy(self.active_seconds_value)
        else:
            target.pop("active_seconds", None)
        if self.inherited_spend_present:
            if self.inherited_spend_is_dict and self.inherited_spend is not None:
                target["inherited_spend"] = self.inherited_spend.to_dict()
            else:
                target["inherited_spend"] = copy.deepcopy(self.inherited_spend_non_dict)
        else:
            target.pop("inherited_spend", None)

    @property
    def raw_active_seconds(self) -> Any:
        return self.active_seconds_value if self.active_seconds_present else None

    @property
    def active_seconds_default_zero(self) -> Any:
        return self.active_seconds_value if self.active_seconds_present else 0

    @property
    def active_seconds_or_zero(self) -> Any:
        return self.active_seconds_value if self.active_seconds_present and self.active_seconds_value else 0

    def with_added_active_seconds(self, delta: int | float, *, treat_falsy_as_zero: bool = False) -> BudgetSlice:
        base = self.active_seconds_or_zero if treat_falsy_as_zero else self.active_seconds_default_zero
        return replace(
            self,
            active_seconds_present=True,
            active_seconds_value=base + delta,
        )

    def inherited_spend_dict(self) -> dict[str, Any]:
        if self.inherited_spend_is_dict and self.inherited_spend is not None:
            return self.inherited_spend.to_dict()
        return {}

    def with_inherited_spend(self, record: Mapping[str, Any] | None) -> BudgetSlice:
        if record is None:
            return replace(
                self,
                inherited_spend_present=False,
                inherited_spend_is_dict=False,
                inherited_spend=None,
                inherited_spend_non_dict=None,
            )
        return replace(
            self,
            inherited_spend_present=True,
            inherited_spend_is_dict=True,
            inherited_spend=InheritedSpendSlice.from_mapping(record),
            inherited_spend_non_dict=None,
        )


def raw_active_seconds(state: Mapping[str, Any] | RunState | Any) -> Any:
    """Equivalent to ``state.get('active_seconds')`` through ``BudgetSlice``."""
    return BudgetSlice.from_state(state).raw_active_seconds


def active_seconds(state: Mapping[str, Any] | RunState | Any) -> Any:
    """Equivalent to ``state.get('active_seconds', 0)`` through ``BudgetSlice``."""
    return BudgetSlice.from_state(state).active_seconds_default_zero


def active_seconds_or_zero(state: Mapping[str, Any] | RunState | Any) -> Any:
    """Equivalent to ``state.get('active_seconds') or 0`` through ``BudgetSlice``."""
    return BudgetSlice.from_state(state).active_seconds_or_zero


def add_active_seconds(
    state: MutableMapping[str, Any] | RunState,
    delta: int | float,
    *,
    treat_falsy_as_zero: bool = False,
) -> None:
    """Charge ``delta`` onto ``state['active_seconds']`` via ``BudgetSlice``."""
    updated = BudgetSlice.from_state(state).with_added_active_seconds(delta, treat_falsy_as_zero=treat_falsy_as_zero)
    target = cast("MutableMapping[str, Any]", state)
    target["active_seconds"] = copy.deepcopy(updated.active_seconds_value)


def inherited_spend_dict(state: Mapping[str, Any] | RunState | Any) -> dict[str, Any]:
    """Return a dict copy of ``state['inherited_spend']`` when it is a mapping, else ``{}``."""
    return BudgetSlice.from_state(state).inherited_spend_dict()


def write_inherited_spend(state: MutableMapping[str, Any] | RunState, record: Mapping[str, Any]) -> None:
    """Persist ``record`` onto ``state['inherited_spend']`` via ``BudgetSlice``."""
    updated = BudgetSlice.from_state(state).with_inherited_spend(record)
    target = cast("MutableMapping[str, Any]", state)
    target["inherited_spend"] = updated.inherited_spend_dict()
