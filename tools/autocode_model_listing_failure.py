"""Typed facts and read-only presentation for the plain Kilo roster timeout.

Only the actual model-free listing producer raises this exception. The retained
runner journal is denial/outcome evidence, never model, retry or source authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


class ModelListingTimeout(util.Paused):
    """Both synchronous roster subprocess attempts ended before any model call."""

    def __init__(self, message, *, workspace, config_sha256, direct_child_collected=True):
        super().__init__("PAUSED_TRANSPORT_UNVERIFIED", message)
        self.workspace = str(Path(workspace).resolve()) if workspace is not None else None
        self.provider = "kilocode"
        self.command = ("kilo", "models")
        self.attempt_bounds = (30, 60)
        self.config_sha256 = config_sha256
        self.direct_child_collected = direct_child_collected


def blocked(state: RunState):
    resolver = state.get("resolver") or {}
    receipts = resolver.get("operational_receipts") or {}
    for identity in reversed(resolver.get("operational_diagnostics") or []):
        record = receipts.get(identity)
        if not isinstance(record, dict):
            continue
        if (
            record.get("runner_owned") is True
            and record.get("engine") == "runner"
            and (record.get("receipt") or {}).get("scope") == "model_listing"
        ):
            return record if (record.get("decision") or {}).get("action") == "blocked" else None
    return None


def presentation(state: RunState):
    """Explain the recorded outcome; never turn it into a person decision."""
    if blocked(state):
        return {
            "category": "blocked",
            "title": "AutoCode is blocked on its model listing",
            "what_happened": "AutoCode could not confirm that its startup check has stopped. It kept the saved work and will not start another step.",
        }
    return None
