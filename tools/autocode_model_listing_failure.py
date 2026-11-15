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
    from . import autocode_operational_block_view as operational_block
    from . import autocode_util as util
except ImportError:
    import autocode_operational_block_view as operational_block
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
    return operational_block.receipt(state, "model_listing")


def presentation(state: RunState):
    """Explain the recorded outcome; never turn it into a person decision."""
    if blocked(state):
        return operational_block.presentation(state)
    return None
