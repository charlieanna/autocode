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
and is not blocked. This runtime helper uses files, ``fcntl`` and process identity.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import sys
from pathlib import Path

try:
    from . import autocode_process as processes
except ImportError:
    import autocode_process as processes
from typing import Any

STATUS = "PAUSED_WORKSPACE_BUSY"
_writer_handles: dict[Any, Any] = {}


class CheckoutBusy(RuntimeError):
    """Another run's agents are working in this checkout; nothing was changed."""

    status = STATUS


def busy_message(workspace) -> str:
    other = holder(workspace).get("run_dir")
    return (
        f"Another AutoCode run is working in this checkout{f' ({other})' if other else ''}; "
        "nothing was changed. Run the same command again once it stops, or give each task its own "
        "worktree by starting it without --in-place."
    )


def lock_path(workspace) -> Path:
    return Path(workspace) / ".autocode" / "writer.lock"


def holder(workspace) -> dict:
    """Who last held the checkout ({} if unknown); only meaningful while it is held."""
    try:
        value = json.loads(lock_path(workspace).read_text() or "{}")
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def orphaned_workers(previous):
    """A controller's flock can die before its provider; retain that ownership.

    Consult only the last holder's durable, birth-identified process receipt.
    Unreadable receipts remain busy; stale PIDs never establish ownership.
    """
    if not previous.get("run_dir"):
        return False
    marker = Path(previous["run_dir"]) / "active-processes.json"
    try:
        saved = json.loads(marker.read_text())
        if not isinstance(saved, dict):
            return True
        return not saved.get("processes") or bool(processes.live_processes(saved["processes"]))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError, processes.ProcessError):
        return True


def child_options(workspace, options):
    """Keep the kernel lock alive across a crash before the first process receipt.

    Popen explicitly inherits only this descriptor and any already allowed
    descriptors. A provider's exit closes its copy. The durable process receipt
    covers providers and descendants after supervision has recorded them.
    """
    handle = _writer_handles.get(str(Path(workspace).resolve()))
    if handle is None:
        return options
    return {
        **options,
        "pass_fds": tuple(dict.fromkeys((*options.get("pass_fds", ()), handle.fileno()))),
        "close_fds": True,
    }


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
        # Check under the lock before overwriting the former holder.
        if orphaned_workers(holder(workspace)):
            message = busy_message(workspace)
            fcntl.flock(handle, fcntl.LOCK_UN)
            if busy is None:
                raise CheckoutBusy(message)
            print(message, file=sys.stderr, flush=True)
            raise busy
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps({"run_dir": str(run_dir), "pid": os.getpid(), "since": dt.datetime.now(dt.UTC).isoformat()})
            )
            handle.flush()
            _writer_handles[str(Path(workspace).resolve())] = handle
            yield
        finally:
            _writer_handles.pop(str(Path(workspace).resolve()), None)
            if not orphaned_workers({"run_dir": str(run_dir)}):
                handle.seek(0)
                handle.truncate()
            # Close our copy without LOCK_UN: a surviving child's copy must
            # retain the kernel lock even before its first receipt exists.
