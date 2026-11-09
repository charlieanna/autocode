"""Passive timings for exclusive runner preparation and report-loading leaves.

These observations grant no authority. A phase closes once when its Python
scope exits; a process crash leaves it unmeasured rather than inventing a finish.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_efficiency as efficiency
    from . import autocode_util as util
    from .providers import kilocode_probe
except ImportError:
    import autocode_efficiency as efficiency
    import autocode_util as util
    from providers import kilocode_probe


@contextmanager
def measure(state: RunState, phase, *, stage=None, attempt=None):
    """Record a leaf's actual interval, including ordinary refusals/exceptions.

    Stage/attempt links live only in provenance: a top-level attempt_id would
    attribute the entire provider attempt to deterministic_setup. The caller
    retains the ledger through its existing state persistence boundaries.
    """
    started_at, started = util.now(), time.monotonic()
    provenance = {
        "phase": phase,
        "stage": stage if stage is not None else state.get("next_stage"),
        "iteration": state.get("iteration"),
        "attempt": attempt,
        "outcome": "completed",
    }
    checks = []
    try:
        with kilocode_probe.capture() as checks:
            yield
    except BaseException as error:
        status = getattr(error, "status", None)
        provenance.update(
            outcome="refused" if isinstance(status, str) and status.startswith("PAUSED_") else "exception",
            exception_type=type(error).__name__,
        )
        if isinstance(status, str):
            provenance["refusal_status"] = status
        raise
    finally:
        finished_at = util.now()
        provenance["duration_seconds"] = time.monotonic() - started
        if checks:
            provenance["model_free_checks"] = checks
        efficiency.record_observation(
            state,
            event_id="phase:" + uuid.uuid4().hex,
            kind="activity",
            category="deterministic_setup",
            provenance=provenance,
            started_at=started_at,
            finished_at=finished_at,
        )
