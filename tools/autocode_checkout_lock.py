"""One run's agents at a time in a checkout.

A run already holds its own lock (``autocode_support.run_lock``), but two different
runs started with ``--in-place`` in one checkout used to build in it at the same
time, each overwriting the other's source. ``exclusive`` holds the checkout's writer
lock, ``<workspace>/.autocode/writer.lock``, for as long as a run's agents work.
It is the file ``autocode_support.workspace_lock`` locks, so Builder workers and
``autocode clean-worktrees`` respect it too. The holder is written into the file,
so a run that finds the checkout busy can say which run has it.

A run that finds it busy changes nothing: it says who holds the checkout and exits,
and the same command works once the other run's agents have stopped. Saving an
answer, approval or other action that launches no agent does not need the checkout
and is not blocked. Lower layer only: files and ``fcntl``.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import sys

STATUS = "PAUSED_WORKSPACE_BUSY"


class CheckoutBusy(RuntimeError):
    """Another run's agents are working in this checkout; nothing was changed."""
    status = STATUS


def busy_message(workspace) -> str:
    other = holder(workspace).get("run_dir")
    return (f"Another AutoCode run is working in this checkout{f' ({other})' if other else ''}; "
            "nothing was changed. Run the same command again once it stops, or give each task its own "
            "worktree by starting it without --in-place.")


def lock_path(workspace) -> Path:
    return Path(workspace) / ".autocode" / "writer.lock"


def holder(workspace) -> dict:
    """Who last held the checkout ({} if unknown); only meaningful while it is held."""
    try:
        return json.loads(lock_path(workspace).read_text() or "{}")
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def exclusive(workspace, run_dir, *, busy=None):
    """Hold the checkout for ``run_dir``'s agents.

    If another run holds it, print why to stderr and raise ``busy`` (the caller's
    clean-exit exception, so it saves nothing), or CheckoutBusy when none is given.
    """
    path = lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message = busy_message(workspace)
            if busy is None:
                raise CheckoutBusy(message) from None
            print(message, file=sys.stderr, flush=True)
            raise busy from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"run_dir": str(run_dir), "pid": os.getpid(),
                                     "since": dt.datetime.now(dt.timezone.utc).isoformat()}))
            handle.flush()
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle, fcntl.LOCK_UN)
