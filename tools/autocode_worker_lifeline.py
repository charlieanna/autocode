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
from pathlib import Path
import time
import uuid

try:
    from . import autocode_process as processes, autocode_supervision_cli as supervision_cli
    from . import autocode_util as util
except ImportError:
    import autocode_process as processes
    import autocode_supervision_cli as supervision_cli
    import autocode_util as util


# A BUILT result is adopted only from a worker whose keeper verified its cleanup, or
# never armed: a launch that died before guarding itself did no work.
ADOPTABLE = frozenset({'discharged', 'stopped', 'never_armed'})


def prepare(run_dir):
    """A fresh lifeline: (record, read_fd, write_fd), its declaration already in the pipe."""
    pid = os.getpid()
    row = processes.process_table({pid}).get(pid)
    if not row:
        raise processes.ProcessError('Cannot capture the Orchestrator birth identity')
    owner = processes.identity(row)
    nonce = uuid.uuid4().hex
    receipt = Path(run_dir).resolve() / 'lifeline' / f'{nonce}-supervision.json'
    if receipt.exists():
        raise processes.ProcessError('Builder lifeline receipt already exists; retain the previous launch')
    data = (json.dumps({'schema': 1, 'owner': owner, 'nonce': nonce, 'receipt': str(receipt),
                        'deadline': None, 'timeout_seconds': None}, separators=(',', ':')) + '\n').encode()
    if len(data) > supervision_cli.MAX_DECLARATION_BYTES:
        raise processes.ProcessError('Builder lifeline declaration exceeds its bound')
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, data)  # far below PIPE_BUF: one complete write into an empty pipe
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    record = {'schema': 1, 'nonce': nonce, 'owner': owner, 'receipt': str(receipt), 'started_at': util.now()}
    return record, read_fd, write_fd


def argv(read_fd):
    """Appended last: the worker script stays argv[1] for anything that matches it."""
    return [supervision_cli.FLAG, str(read_fd)]


def read(record):
    """The keeper's receipt for this launch; None when absent or not bound to it."""
    try:
        path = Path(record['receipt'])
        if path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text())
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (not isinstance(value, dict) or value.get('schema') != 1
            or any(value.get(key) != record.get(key) for key in ('nonce', 'owner'))):
        return None
    return value


def classify(value):
    """running, discharged, stopped or uncertain, for an authenticated receipt (or None)."""
    if not isinstance(value, dict):
        return 'uncertain'
    phase = value.get('phase')
    if phase in ('armed', 'stopping'):
        return 'running'
    if value.get('cleanup_error') is not None:
        return 'uncertain'
    if phase == 'discharged' and value.get('cause') == 'controller_finished':
        return 'discharged'
    return 'stopped' if phase == 'stopped' else 'uncertain'


def settled(record):
    """never_armed when the keeper never wrote a receipt; otherwise classify() of it."""
    try:
        present = Path(record['receipt']).exists()
    except (KeyError, TypeError):
        return 'uncertain'
    # A receipt is never removed: check presence first so a late arm reads as armed.
    return classify(read(record)) if present else 'never_armed'


def _identity(row):
    return (isinstance(row, dict) and type(row.get('pid')) is int and row['pid'] > 0
            and type(row.get('birth_identity')) in (int, float))


def owned(record, recorded=()):
    """What the Orchestrator still treats as one worker's: its sampled tree, its keeper and what that recorded."""
    rows = list(recorded)
    value = read(record) if isinstance(record, dict) else None
    if value:
        inventory = value.get('processes') if isinstance(value.get('processes'), list) else []
        rows += [row for row in (value.get('keeper'), *inventory) if _identity(row)]
    return rows


def await_settled(records, seconds):
    """Wait, bounded, while any keeper is still cleaning up; a later check decides what remains."""
    deadline = time.monotonic() + seconds
    while (any(settled(record) == 'running' for record in records if record)
           and time.monotonic() < deadline):
        time.sleep(.05)
