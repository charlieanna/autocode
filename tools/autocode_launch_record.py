"""What a new run is doing before its first checkpoint exists.

A launched run saves `state.json` only after it has prepared the workspace and resolved which model
each stage runs, so `--status` has nothing to answer with during exactly the window a person watches
after their first message. Saving a checkpoint earlier is not available: the launch's cleanup paths
key on `state.json` (`autocode_workspaces.discard_unused`, `autocode_run_destination.discard_unsaved`)
to remove a task worktree and branch that never produced a run, and a half-boot checkpoint would make
every refused launch permanent litter.

So the launcher leaves a note instead. It is display only: it names no action, holds no authority, and
is never read as a checkpoint. `read` says whether the process that wrote it still lives, which is how
a client tells a slow start from a launcher that died. The run clears it as soon as its real
checkpoint exists, so a status read can only see one of the two.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

VERSION = 1
NAME = "launch.json"

# The boot phases, in the order a launch passes through them, each with the words a client shows.
# These are not stages: no model runs in any of them, so they are not in autocode_roles.
PHASES = {
    "preparing_run": "preparing the run",
    "resolving_models": "resolving which model each stage runs",
}


def path(run_dir) -> Path:
    return Path(run_dir) / NAME


def write(run_dir, phase, *, workspace, now=None) -> None:
    """Say what this launch is doing now; a note that cannot be saved never stops a run.

    `started_at` is kept from the earlier note so a client can tell the whole wait, not this phase.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown launch phase: {phase}")
    location = path(run_dir)
    moment = now or util.now()
    try:
        earlier = read(run_dir) or {}
        util.atomic_json(
            location,
            {
                "version": VERSION,
                "workspace": str(Path(workspace).resolve()),
                "run_dir": str(Path(run_dir).resolve()),
                "phase": phase,
                "label": PHASES[phase],
                "at": moment,
                "started_at": earlier.get("started_at") or moment,
                "pid": os.getpid(),
            },
        )
    except OSError:
        with suppress(OSError):
            location.unlink(missing_ok=True)  # never leave half a note behind


def read(run_dir) -> dict | None:
    """The launch note with `owner_alive`, or None when there is none worth showing."""
    try:
        record = json.loads(path(run_dir).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("version") != VERSION or record.get("phase") not in PHASES:
        return None
    alive = None
    if isinstance(record.get("pid"), int):
        try:
            os.kill(record["pid"], 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:  # another user's process: it lives
            alive = True
        except OSError:
            alive = None
    return {**record, "owner_alive": alive, "label": PHASES[record["phase"]]}


def clear(run_dir) -> None:
    """Drop the note once the run has a checkpoint to read instead."""
    with suppress(OSError):
        path(run_dir).unlink(missing_ok=True)
