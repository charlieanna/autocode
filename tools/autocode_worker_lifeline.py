"""A parallel Builder worker never outlives the Orchestrator that launched it (#454).

The Orchestrator holds the only writer of a pipe the worker inherits. The worker
guards itself on that pipe with an independent keeper (autocode_supervision_cli.guard):
when the Orchestrator dies, the keeper interrupts the worker so it saves its pause,
then stops everything it recorded. This is the Orchestrator's side: declare the
lifeline, then read and classify the keeper's receipt. It never reads or writes run
state; the caller stores the record as the worker row's ``supervision``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

try:
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_process as processes
    from . import autocode_supervision_cli as supervision_cli
    from . import autocode_util as util
except ImportError:
    import autocode_checkout_lock as checkout_lock
    import autocode_process as processes
    import autocode_supervision_cli as supervision_cli
    import autocode_util as util


# A BUILT result is adopted only from a worker whose keeper verified its cleanup, or
# never armed: a launch that died before guarding itself did no work.
ADOPTABLE = frozenset({"discharged", "stopped", "never_armed"})

# How long the worker's keeper allows between its SIGTERM and SIGKILL after owner loss. The
# SIGTERM interrupts the worker's running stage, whose provider guard waits up to 8 s for the
# stage's own keeper (autocode_supervision.launch: keeper.wait(timeout=8)); that keeper allows
# its provider 2 s between freeze and kill before it verifies the tree. 8 + 2 = 10 s, plus a
# margin for the worker to save its pause and result on a loaded host.
KILL_AFTER_SECONDS = 15

# How long the Orchestrator waits for a keeper it has released to publish its receipt: the window the
# worker declared, then the keeper verifying the tree and writing the receipt (its trigger and join
# bounds, a few seconds on a loaded host). The Orchestrator's own last resort, freezing the worker's
# tree, would reach that keeper mid-cleanup, so it comes only after this.
SETTLE_SECONDS = KILL_AFTER_SECONDS + 5


def prepare(run_dir, workspace=None):
    """A fresh lifeline: (record, read_fd, write_fd), its declaration already in the pipe."""
    pid = os.getpid()
    row = processes.process_table({pid}).get(pid)
    if not row:
        raise processes.ProcessError("Cannot capture the Orchestrator birth identity")
    owner = processes.identity(row)
    nonce = uuid.uuid4().hex
    receipt = Path(run_dir).resolve() / "lifeline" / f"{nonce}-supervision.json"
    if receipt.exists():
        raise processes.ProcessError("Builder lifeline receipt already exists; retain the previous launch")
    packet = {
        "schema": 1,
        "owner": owner,
        "nonce": nonce,
        "receipt": str(receipt),
        "deadline": None,
        "timeout_seconds": None,
        "kill_after_seconds": KILL_AFTER_SECONDS,
    }
    lease = checkout_lock.lease(workspace)
    if lease is not None:
        packet["checkout_lease"] = lease
    data = (json.dumps(packet, separators=(",", ":")) + "\n").encode()
    if len(data) > supervision_cli.MAX_DECLARATION_BYTES:
        raise processes.ProcessError("Builder lifeline declaration exceeds its bound")
    read_fd, write_fd = os.pipe()
    record = {"schema": 1, "nonce": nonce, "owner": owner, "receipt": str(receipt), "started_at": util.now()}
    try:
        checkout_lock.retain_worker(workspace, record)
        os.write(write_fd, data)  # far below PIPE_BUF: one complete write into an empty pipe
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    return record, read_fd, write_fd


def argv(read_fd):
    """Appended last: the worker script stays argv[1] for anything that matches it."""
    return [supervision_cli.FLAG, str(read_fd)]


def read(record):
    """The keeper's receipt for this launch; None when absent or not bound to it."""
    try:
        path = Path(record["receipt"])
        if path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text())
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or any(value.get(key) != record.get(key) for key in ("nonce", "owner"))
    ):
        return None
    return value


def classify(value):
    """running, discharged, stopped or uncertain, for an authenticated receipt (or None)."""
    if not isinstance(value, dict):
        return "uncertain"
    phase = value.get("phase")
    if phase in ("armed", "stopping"):
        return "running"
    if value.get("cleanup_error") is not None:
        return "uncertain"
    if phase == "discharged" and value.get("cause") == "controller_finished":
        return "discharged"
    return "stopped" if phase == "stopped" else "uncertain"


def settled(record):
    """never_armed when the keeper never wrote a receipt; otherwise classify() of it."""
    try:
        present = Path(record["receipt"]).exists()
    except (KeyError, TypeError):
        return "uncertain"
    # A receipt is never removed: check presence first so a late arm reads as armed.
    return classify(read(record)) if present else "never_armed"


def _identity(row):
    return (
        isinstance(row, dict)
        and type(row.get("pid")) is int
        and row["pid"] > 0
        and type(row.get("birth_identity")) in (int, float)
    )


def owned(record, recorded=()):
    """What the Orchestrator still treats as one worker's: its sampled tree, its keeper and what that recorded."""
    rows = list(recorded)
    value = read(record) if isinstance(record, dict) else None
    if value:
        inventory = value.get("processes") if isinstance(value.get("processes"), list) else []
        rows += [row for row in (value.get("keeper"), *inventory) if _identity(row)]
    return rows


def pending(record):
    """Its keeper is still cleaning up: the receipt reads armed or stopping and a keeper is alive to finish it.

    A keeper that died unpublished leaves the receipt running for good; waiting for it would only delay.
    """
    if settled(record) != "running":
        return False
    keeper = (read(record) or {}).get("keeper")
    return _identity(keeper) and bool(processes.live_processes([keeper]))


def await_settled(records, seconds=SETTLE_SECONDS):
    """Wait, bounded, while any keeper is still cleaning up; a later check decides what remains."""
    deadline = time.monotonic() + seconds
    while any(pending(record) for record in records if record) and time.monotonic() < deadline:
        time.sleep(0.05)
