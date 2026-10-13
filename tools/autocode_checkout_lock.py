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
import math
import os
import stat
import sys
from pathlib import Path

try:
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_process as processes
    import autocode_util as util
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
        "nothing was changed. Run the same command again once its workers have stopped and cleanup can be verified, "
        "or give each task its own "
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
    worker_marker = Path(previous["run_dir"]) / "checkout-workers.json"
    try:
        if worker_marker.stat().st_size > 1024 * 1024:
            return True
        records = json.loads(worker_marker.read_text())
        if not isinstance(records, list):
            return True
    except FileNotFoundError:
        records = []
    except (OSError, ValueError):
        return True
    for record in records:
        try:
            path = Path(record["receipt"])
            if path.stat().st_size > 1024 * 1024:
                return True
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or value.get("schema") != 1
                or any(value.get(key) != record.get(key) for key in ("nonce", "owner"))
                or not isinstance(value.get("processes"), list)
                or value.get("phase") not in ("armed", "stopping", "stopped", "discharged", "uncertain")
            ):
                return True
            rows = [value["keeper"], value["provider"], *value["processes"]]
            if any(
                not isinstance(row, dict)
                or type(row.get("pid")) is not int
                or row["pid"] <= 0
                or type(row.get("birth_identity")) not in (int, float)
                or not math.isfinite(row["birth_identity"])
                or row["birth_identity"] <= 0
                for row in rows
            ) or processes.live_processes(rows):
                return True
        except FileNotFoundError:
            continue  # acquisition plus the inherited descriptor proves a child never armed
        except (OSError, ValueError, KeyError, TypeError, processes.ProcessError):
            return True
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
    handle = _writer_handles.get(str(Path(workspace).resolve())) if workspace is not None else None
    if handle is None:
        return options
    return {
        **options,
        "pass_fds": tuple(dict.fromkeys((*options.get("pass_fds", ()), handle.fileno()))),
        "close_fds": True,
    }


def lease(workspace):
    """The held descriptor's capability, for an independently guarded worker.

    A worker passes this copy only to its keeper, then closes it before doing
    work. Closing the controller never unlocks the keeper's copy.
    """
    handle = _writer_handles.get(str(Path(workspace).resolve())) if workspace is not None else None
    if handle is None:
        return None
    row = os.fstat(handle.fileno())
    return {
        "fd": handle.fileno(),
        "path": str(lock_path(Path(workspace).resolve())),
        "device": row.st_dev,
        "inode": row.st_ino,
        "owner_pid": os.getpid(),
    }


def retain_worker(workspace, record):
    """Retain a worker's receipt before it inherits the checkout capability.

    A keeper with uncertain cleanup may exit within its existing bound. Its
    receipt still prevents admission while recorded actors live or inspection
    is unavailable, without keeping an idle lease process around forever.
    """
    handle = _writer_handles.get(str(Path(workspace).resolve())) if workspace is not None else None
    if handle is None:
        return
    # The lock's inode must stay stable and its holder must survive a crash
    # during this write. Publish references separately with atomic replacement.
    path = Path(holder(workspace)["run_dir"]) / "checkout-workers.json"
    try:
        previous = json.loads(path.read_text())
    except FileNotFoundError:
        previous = []
    if not isinstance(previous, list):
        raise processes.ProcessError("Checkout worker ownership is unreadable; retain it before another launch")
    records = [*previous, record]
    if len((json.dumps(records, indent=2, sort_keys=True) + "\n").encode()) > 1024 * 1024:
        raise processes.ProcessError("Checkout worker ownership exceeds its read bound; no further worker was launched")
    util.atomic_json(path, records)


def validate_lease(value, owner):
    """Validate an inherited checkout capability before a worker admits work."""
    if not isinstance(value, dict) or type(value.get("fd")) is not int or value["fd"] <= 2:
        raise ValueError("Checkout lease must be an inherited descriptor")
    path = value.get("path")
    if (
        not isinstance(path, str)
        or not Path(path).is_absolute()
        or Path(path).parts[-2:] != (".autocode", "writer.lock")
    ):
        raise ValueError("Checkout lease must identify the checkout writer lock")
    row = os.fstat(value["fd"])
    expected = Path(path).stat()
    if (
        not stat.S_ISREG(row.st_mode)
        or type(value.get("device")) is not int
        or type(value.get("inode")) is not int
        or (row.st_dev, row.st_ino) != (expected.st_dev, expected.st_ino)
        or (row.st_dev, row.st_ino) != (value["device"], value["inode"])
        or type(value.get("owner_pid")) is not int
        or value["owner_pid"] != owner.get("pid")
    ):
        raise ValueError("Checkout lease does not match its declared owner and file")
    return value["fd"]


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
            if not orphaned_workers(holder(workspace)):
                handle.seek(0)
                handle.truncate()
            # Close our copy without LOCK_UN: a surviving child's copy must
            # retain the kernel lock even before its first receipt exists.
