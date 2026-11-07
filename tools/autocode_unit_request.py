"""Prepare a unit request with invocation-local authorization and checked guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


try:
    from . import autocode_phase_timing as phase_timing
    from . import autocode_stuck_job as stuck
except ImportError:
    import autocode_phase_timing as phase_timing
    import autocode_stuck_job as stuck


def prepare(state: RunState, stage, state_path, schema_dir, *, select_unit, retry_authorization=None):
    with phase_timing.measure(state, "request_assembly", stage=stage):
        options = {"retry_authorization": retry_authorization} if stage == "astra_resolve" else {}
        request = select_unit(stage).prepare(state, stage, state_path, schema_dir, **options)
        return stuck.with_guidance(state, stage, request)
