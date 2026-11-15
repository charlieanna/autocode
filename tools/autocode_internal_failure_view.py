"""One read-only internal-failure projection for the public run readers.

Domain facts use the pure runner-receipt projection directly. This view also
retains the existing authenticated provider-startup presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_operational_block_view as blocked
    from . import autocode_startup_timeout_view as startup
except ImportError:
    import autocode_operational_block_view as blocked
    import autocode_startup_timeout_view as startup

receipt = blocked.receipt


def presentation(state: RunState):
    return blocked.presentation(state) or startup.presentation(state)
