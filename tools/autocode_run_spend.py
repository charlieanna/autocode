"""The whole run's known spend against the person's cost cap, read from one place (#721).

A parallel Builder runs as a run of its own (autocode_dispatch.prepare): it starts with a copy of
the parent's settings and no stages, so its cost checks compared the parent's whole cap with that
one member's spend. Every member of a batch could spend up to the full cap, and a member started its
report repair, Investigator or next attempt after the run as a whole had passed the cap. Raising the
cap on the parent never reached the copy either.

``known(state)`` is the one answer the launch guard, the boundary check and the running-stage
watch read:

  - for a run with no open batch, its own known spend (autocode_cost.spent) and its saved cap;
  - for the parent of an open batch, or for one of its members, the parent's spend outside the batch
    plus every member's finished attempts and, for each member other than the caller, the attempt it
    is running now, priced from its own event file as autocode_cost_watch prices a running stage.
    The cap is the parent's saved cap, which is the one the person raises.

``guard(state)`` stops a launch once that spend has reached the cap: no new paid work starts past
the cap, whichever path asks for it (a stage, a report repair, an Investigator, a member's retry).
Unpriced work counts as nothing here, as everywhere the cap is compared (autocode_cost).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_support as support
    from . import autocode_util as util
except ImportError:
    import autocode_cost as cost
    import autocode_support as support
    import autocode_util as util

STATUS = "PAUSED_COST_LIMIT"


def _read(path):
    try:
        value = util.read(Path(path))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _same(left, right):
    try:
        return bool(left) and bool(right) and Path(str(left)).resolve() == Path(str(right)).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def running_usd(state: RunState) -> float:
    """What the attempt `state` is running now has spent so far, from its own event file; 0 when none runs
    or it cannot be priced."""
    record = state.get("active_stage") if isinstance(state, dict) else None
    if not isinstance(record, dict) or not record or record.get("finished_at") or record.get("accounted"):
        return 0.0
    events = record.get("events")
    if not events or not Path(str(events)).is_file():
        return 0.0
    try:
        metrics = support.event_metrics(Path(str(events)))
    except (OSError, ValueError, TypeError):
        return 0.0
    running = {**record, "metrics": metrics, "finished_at": util.now()}
    return cost.spent({"settings": state.get("settings"), "stages": [running]})


def _root(state: RunState):
    """The run that owns the cap, and the open batch spent under it: (root state, batch or None)."""
    parent_dir = state.get("parent_run")
    if parent_dir and state.get("parent_batch"):
        parent = _read(Path(str(parent_dir)) / "state.json")
        if parent is not None:
            batch = parent.get("orchestration_batch")
            if isinstance(batch, dict) and batch.get("id") == state.get("parent_batch"):
                return parent, batch
            return parent, None
        return state, None
    batch = state.get("orchestration_batch")
    return state, batch if isinstance(batch, dict) and batch.get("id") else None


def known(state: RunState, *, own_running=False):
    """{"spent", "cap"}: the whole run's known spend in USD and the person's cap (None for no cap).

    `own_running` also counts the attempt `state` itself is running; the running-stage watch prices that
    one from its own events and leaves it out here."""
    state = state if isinstance(state, dict) else {}
    root, batch = _root(state)
    limit = cost.cap(root.get("settings"))
    if limit is None and root is not state:
        limit = cost.cap(state.get("settings"))
    if batch is None:
        spent = cost.spent(root if root is not state else state)
        if root is not state:  # a member whose batch closed: the parent holds its attempts
            spent += running_usd(state) if own_running else 0.0
        elif own_running:
            spent += running_usd(state)
        return {"spent": spent, "cap": limit}
    batch_id = batch.get("id")
    outside = [row for row in root.get("stages") or [] if isinstance(row, dict) and row.get("batch_id") != batch_id]
    spent = cost.spent({**root, "stages": outside, "active_stage": None})
    for row in batch.get("workers") or []:
        if not isinstance(row, dict):
            continue
        caller = _same(row.get("workspace"), state.get("workspace")) and root is not state
        member = state if caller else _read(Path(str(row.get("run_dir") or "")) / "state.json")
        if member is None:
            # Unreadable: fall back to the attempts the parent already copied for this member.
            copied = [
                record
                for record in root.get("stages") or []
                if isinstance(record, dict)
                and record.get("batch_id") == batch_id
                and record.get("worker_milestone") == row.get("milestone_id")
            ]
            spent += cost.spent({"settings": root.get("settings"), "stages": copied})
            continue
        spent += cost.spent({**member, "inherited_spend": None})
        if not caller or own_running:
            spent += running_usd(member)
    return {"spent": spent, "cap": limit}


def exceeded(state: RunState):
    """(spent, cap) once the whole run's known spend has reached the cap, else None."""
    answer = known(state)
    if answer["cap"] is None or answer["spent"] < answer["cap"]:
        return None
    return answer["spent"], answer["cap"]


def guard(state: RunState, job="the next stage"):
    """Refuse a launch once the run has spent its cap: support.Paused(PAUSED_COST_LIMIT) before anything starts."""
    if reached := exceeded(state):
        raise support.Paused(
            STATUS,
            f"Spend so far {cost.usd(reached[0])} reached the {cost.usd(reached[1])} cost cap; {job} did not launch",
        )
