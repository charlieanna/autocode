"""Typed fixture sub-envelope isolating retired ``_fixture_*`` hooks (#3468).

Legacy fixtures may still hold top-level ``_fixture_*`` keys; ``FixtureEnvelope``
reads ``state["_fixture"]`` first, falls back to those legacy keys without
mutating inputs, preserves unknown subkeys round-trip, and writes back solely
under ``state["_fixture"]``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import FixtureState, RunState

ENVELOPE_KEY = "_fixture"

LEGACY_KEYS: Mapping[str, str] = {
    "accepted": "_fixture_accepted",
    "attempt": "_fixture_attempt",
    "interventions": "_fixture_interventions",
    "model_confirm_mode": "_fixture_model_confirm_mode",
    "model_reconcile_proposed": "_fixture_model_reconcile_proposed",
    "model_reconcile_reads": "_fixture_model_reconcile_reads",
    "model_reconcile_role": "_fixture_model_reconcile_role",
    "monitor": "_fixture_monitor",
    "pause_reconcile_reads": "_fixture_pause_reconcile_reads",
    "saved_diff": "_fixture_saved_diff",
    "verification": "_fixture_verification",
}


@dataclass(frozen=True)
class FixtureEnvelope:
    """Lossless, immutable view of the ``_fixture`` sub-envelope."""

    fields: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def from_state(cls, state: Mapping[str, Any] | RunState) -> FixtureEnvelope:
        merged: dict[str, Any] = {}
        mapping = cast("Mapping[str, Any]", state) if isinstance(state, Mapping) else None
        raw = mapping.get(ENVELOPE_KEY) if mapping is not None else None
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                merged[str(key)] = copy.deepcopy(value)
        if mapping is not None:
            for field, legacy_key in LEGACY_KEYS.items():
                if field not in merged and legacy_key in mapping:
                    merged[field] = copy.deepcopy(mapping[legacy_key])
        return cls(fields=tuple(merged.items()))

    def get(self, name: str, default: Any = None) -> Any:
        for key, value in self.fields:
            if key == name:
                return copy.deepcopy(value)
        return default

    def to_dict(self) -> FixtureState:
        return cast("FixtureState", {key: copy.deepcopy(value) for key, value in self.fields})

    def with_field(self, name: str, value: Any) -> FixtureEnvelope:
        updated = {key: copy.deepcopy(existing) for key, existing in self.fields}
        updated[name] = copy.deepcopy(value)
        return FixtureEnvelope(fields=tuple(updated.items()))

    def without_field(self, name: str) -> FixtureEnvelope:
        return FixtureEnvelope(fields=tuple((key, copy.deepcopy(value)) for key, value in self.fields if key != name))

    def write_to_state(self, state: MutableMapping[str, Any] | RunState, *, strip_legacy: bool = True) -> None:
        target = cast("MutableMapping[str, Any]", state)
        if self.fields:
            target[ENVELOPE_KEY] = self.to_dict()
        else:
            target.pop(ENVELOPE_KEY, None)
        if strip_legacy:
            for legacy_key in LEGACY_KEYS.values():
                target.pop(legacy_key, None)


def read_field(state: Mapping[str, Any] | RunState, name: str, default: Any = None) -> Any:
    return FixtureEnvelope.from_state(state).get(name, default)


def write_field(
    state: MutableMapping[str, Any] | RunState, name: str, value: Any, *, strip_legacy: bool = True
) -> None:
    FixtureEnvelope.from_state(state).with_field(name, value).write_to_state(state, strip_legacy=strip_legacy)
