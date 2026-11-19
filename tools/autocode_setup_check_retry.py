"""Wait for a loaded host when a model-free setup check only ran out of time, then try again.

Kilo's catalog and policy checks (providers.kilocode_probe) and the sandbox's own checks
(autocode_tool_containment.setup_check) already run once more inside one bounded budget when they
run out of time. A host at a load average of about 165 spent both attempts of each and the run paused
with PAUSED_TOOL_CONTAINMENT, asking the person to resume: a pause about AutoCode's own machinery
that AutoCode can settle itself (live VS Code test, 2026-11-18). Running out of time says nothing
about the setup, no model request was launched, and the same check answered when the run was
resumed by hand.

So the run waits and asks again, a bounded number of times, before that pause stands:

- ``recover`` is a row of the shared recovery chain (autocode_recovery_dispatch). It takes a stage
  launch whose preparation stopped this way, before any provider started (no ``active_stage``).
- ``patiently`` runs a check made at a stage boundary (the selected tools' admission) the same way.

Both read and write one record, the run's ``setup_check_retries``: what failed, which stage it was
for, when, and how long the run waited. The bound counts consecutive retries since the run last
recorded a stage, so a later stage starts with the whole allowance again. A nonzero exit, a refused
permission or any other answer is not a timeout and is never retried here.

Imports only autocode_util; tests replace ``wait`` rather than the shared ``time.sleep``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

# Consecutive automatic retries for one stage before the pause stands, and the wait before each.
WAITS_SECONDS = (30, 60, 120)
MAX_RETRIES = len(WAITS_SECONDS)
STATUSES = frozenset({"PAUSED_TOOL_CONTAINMENT", "PAUSED_TRANSPORT_UNVERIFIED"})
MAX_REASON = 500


def wait(seconds):
    time.sleep(seconds)


def timed_out(error) -> bool:
    """Whether ``error`` was caused by a model-free setup check that only ran out of time.

    The checks' own exceptions say so with ``MODEL_FREE_TIMEOUT``; the pause that reaches the run
    carries one of them in its cause chain. Matching the attribute, not the class, holds for both
    import paths of the provider modules.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if getattr(type(error), "MODEL_FREE_TIMEOUT", False):
            return True
        error = error.__cause__
    return False


def _consecutive(state: RunState, stage) -> int:
    marker = len(state.get("stages") or [])
    return sum(
        1
        for row in state.get("setup_check_retries") or []
        if row.get("stage") == stage and row.get("stages_recorded") == marker
    )


def _wait(state: RunState, stage, error, *, where) -> bool:
    """Record one retry and wait for it, or return False when the allowance is spent."""
    count = _consecutive(state, stage)
    if count >= MAX_RETRIES:
        return False
    seconds = WAITS_SECONDS[count]
    state.setdefault("setup_check_retries", []).append(
        {
            "at": util.now(),
            "stage": stage,
            "where": where,
            "status": getattr(error, "status", None),
            "retry_number": count + 1,
            "max_retries": MAX_RETRIES,
            "wait_seconds": seconds,
            "stages_recorded": len(state.get("stages") or []),
            "reason": str(error)[:MAX_REASON],
            "decision": "A model-free setup check only ran out of time and no model request was launched; "
            "the host was slow, so AutoCode waits and runs it again",
        }
    )
    return True


def recover(runtime, state: RunState, run_dir, workspace, error) -> bool:
    """Retry a stage launch whose preparation stopped on a check that only ran out of time."""
    if (
        getattr(error, "status", None) not in STATUSES
        or not timed_out(error)
        or state.get("active_stage")
        or (run_dir / "pause-requested").exists()
    ):
        return False
    stage = state.get("next_stage")
    if not _wait(state, stage, error, where="stage_launch"):
        return False
    runtime.write_json(run_dir / "state.json", state)
    row = state["setup_check_retries"][-1]
    print(
        f"{stage}: a model-free setup check ran out of time on a slow host; waiting {row['wait_seconds']}s "
        f"and trying again ({row['retry_number']}/{MAX_RETRIES})",
        flush=True,
    )
    wait(row["wait_seconds"])
    return True


def patiently(check, state: RunState, run_dir, *, persist, where):
    """Run a stage-boundary ``check`` (no arguments), waiting and retrying while it only runs out of time."""
    while True:
        try:
            return check()
        except util.Paused as error:
            if (
                error.status not in STATUSES
                or not timed_out(error)
                or (run_dir / "pause-requested").exists()
                or not _wait(state, state.get("next_stage"), error, where=where)
            ):
                raise
            persist()
            wait(state["setup_check_retries"][-1]["wait_seconds"])
