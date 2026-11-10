"""What a chat says while a run is starting and has saved no checkpoint, so no status view exists.

The core's launch note (``autocode_launch_record``, answered by ``--status`` as ``starting``) names the
phase of a start in progress. It carries no ``view``, so it names no action: this renders it as the one
line the person watches while that start runs, with the time it has taken so far.

The engine and mode are the chat's own choice for the run it just launched, which it knows from the
flags and the mode row it passed. Nothing here reads a configuration, a model list or a status view;
the moment the run has a checkpoint, its own view replaces every word of this line.
"""

from __future__ import annotations

from datetime import UTC, datetime

try:
    from . import autocode_chat_meter as meter
    from . import autocode_chat_setup as setup
except ImportError:
    import autocode_chat_meter as meter
    import autocode_chat_setup as setup

STARTED = "Starting the run"
# The note's writer exited with no checkpoint behind: the start ended, and saying "starting" would wait
# for a view that cannot arrive. It names no action, because the view that would name one never came.
DEAD = "the launcher stopped without saving the run"


def began_at(starting: dict) -> datetime | None:
    """When the start began, from the note's own clock; None when the note does not say."""
    try:
        moment = datetime.fromisoformat(str(starting.get("started_at")))
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def seconds(starting: dict, now: datetime | None = None) -> float | None:
    """How long this start has been going; None when the note names no start it can count from."""
    began = began_at(starting)
    if began is None:
        return None
    return max(0.0, ((now or datetime.now(UTC)) - began).total_seconds())


def line(starting: dict, *, now: datetime | None = None) -> str:
    """The status row for a run still starting: what it is doing now, and for how long it has been."""
    doing = DEAD if starting.get("owner_alive") is False else str(starting.get("label") or "").strip()
    parts = [STARTED, doing or "preparing the run"]
    if engine := str(starting.get("engine") or "").strip():
        parts.append(f"on {setup.ENGINE_LABELS.get(engine, engine)}")  # the engine's word, as /engine names it
    if mode := str(starting.get("mode") or "").strip():
        parts.append(mode)
    if elapsed := meter.duration(seconds(starting, now)):
        parts.append(elapsed)
    return " · ".join(parts)
