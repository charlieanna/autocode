"""Autocode owns Builder implementation and parallel scheduling/integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_dispatch as scheduler
except ImportError:
    import autocode_dispatch as scheduler
from .common import execution_request


def prepare(state: RunState, stage, state_path, schema_dir):
    if stage not in ("astra_plan", "terra"):
        raise ValueError(f"Autocode cannot run {stage}")
    return execution_request(state, stage, state_path, schema_dir)


def dispatch(state: RunState, workspace, run_dir):
    return scheduler.dispatch(state, workspace, run_dir)
