"""Prepare a unit request with invocation-local authorization and checked guidance."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


try:
    from . import autocode_phase_timing as phase_timing
    from . import autocode_publish_guard as publish_guard
    from . import autocode_stuck_job as stuck
except ImportError:
    import autocode_phase_timing as phase_timing
    import autocode_publish_guard as publish_guard
    import autocode_stuck_job as stuck


def prepare(state: RunState, stage, state_path, schema_dir, *, select_unit, retry_authorization=None):
    with phase_timing.measure(state, "request_assembly", stage=stage):
        options = {"retry_authorization": retry_authorization} if stage == "resolve" else {}
        request = select_unit(stage).prepare(state, stage, state_path, schema_dir, **options)
        request = stuck.with_guidance(state, stage, request)
        # Every stage, on every engine, is told it publishes nothing (autocode_publish_guard).
        return replace(request, prompt=publish_guard.with_rule(request.prompt))
