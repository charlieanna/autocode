"""Compatibility projection for generic operational-exhaustion receipts only.

Public run readers use autocode_internal_failure_view for typed-denial and Stop
priority. This generic-only API reads the same saved disposition, without
probing receipt files, and grants no task or recovery authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_operational_disposition as disposition
except ImportError:
    import autocode_operational_disposition as disposition


def presentation(state: RunState):
    return disposition.presentation(state)
