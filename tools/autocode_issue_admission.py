"""How many issue runs are in flight at once, and which waiting issue starts next.

The channel bridge asks this before it starts a labeled issue's run, so labeling
twenty issues starts at most a set number of runs and queues the rest instead of
starting every one at once. Pure functions over status views and plain rows:
nothing here starts, reads or changes a run.

An issue holds its slot from the moment its run starts until that run settles:
done, or stopped with nothing left to ask. A run waiting for a person keeps its
slot, so the number of issues asking for answers at once stays bounded along
with the number of runs using models. Admission only gates new starts; a
person's answer or approval always continues its own run.
"""

from __future__ import annotations

from dataclasses import dataclass

# A controller is doing work in these statuses (the same set `autocode runs` calls working).
WORKING_STATUSES = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER")


def settled(view: dict) -> bool:
    """Whether a run no longer holds a slot: done, or stopped with nothing to ask anyone."""
    if view.get("done"):
        return True
    need = view.get("needs") or {}
    if need.get("new_run_required"):
        return True  # nothing resumes this run, so it is not in flight
    if need.get("kind") and need["kind"] != "continue":
        return False  # waiting for a person keeps the slot
    return view.get("status") not in WORKING_STATUSES


def parse_limit(text: str | None) -> int | None:
    """A configured limit: a whole number of runs, 1 or more; blank means no limit."""
    value = (text or "").strip()
    if not value:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError("must be a whole number of runs, 1 or more")
    return int(value)


@dataclass(frozen=True)
class Admission:
    start: tuple  # the waiting rows to start now, in the order given
    waiting: tuple  # the rows that keep waiting


def admit(waiting, active: int, limit: int | None) -> Admission:
    """Start waiting rows in order while fewer than ``limit`` are active; no limit starts them all."""
    rows = tuple(waiting)
    if limit is None:
        return Admission(start=rows, waiting=())
    free = max(0, limit - max(0, active))
    return Admission(start=rows[:free], waiting=rows[free:])
