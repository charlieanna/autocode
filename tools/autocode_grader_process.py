"""Wait for a grader and its owned descendants before publishing a verdict.

Keep the exited session leader unreaped until its group has been sampled.
Otherwise a fast successful grader can orphan children before ownership is
recorded. The shared supervisor handles birth checks, termination and reaping.
"""
from __future__ import annotations

import os
import signal
import time

import psutil

try:
    from . import autocode_process as processes
except ImportError:
    import autocode_process as processes


def _leader(child):
    process = psutil.Process(child.pid)
    with process.oneshot():
        born = process.create_time()
        row = {'pid': child.pid, 'parent': process.ppid(),
               'birth_identity': processes._birth_identity(process),
               'birth_time': born, 'started': ' '.join(time.ctime(born).split()),
               'state': process.status()}
    if row['parent'] != os.getpid():
        raise processes.ProcessError('Independent grader is no longer an unreaped direct child')
    try:
        row['group'] = os.getpgid(child.pid)
    except ProcessLookupError:
        # The child may exit between the status read and getpgid. Re-observe
        # without the earlier oneshot cache; never accept an unknown state or
        # transfer the saved identity to another process.
        observation_deadline = time.monotonic() + 1
        while True:
            fresh = psutil.Process(child.pid)
            if processes._birth_identity(fresh) != row['birth_identity']:
                raise processes.ProcessError('Cannot verify independent grader zombie identity: birth changed')
            parent, status = fresh.ppid(), fresh.status()
            if parent == os.getpid() and status == psutil.STATUS_ZOMBIE:
                break
            if time.monotonic() >= observation_deadline:
                raise processes.ProcessError('Cannot verify independent grader zombie identity: '
                                             f'parent={parent}, state={status}')
            time.sleep(.005)
        row['state'] = psutil.STATUS_ZOMBIE
        # macOS hides pgid for zombies. The caller launched this private
        # session, and the unreaped direct child's PID cannot have been reused.
        row['group'] = child.pid
    return row


def _capture_group(tree, child, root):
    current = _leader(child)
    if not processes.matches(root, current) or current['group'] != child.pid:
        raise processes.ProcessError('Independent grader identity changed before cleanup')
    candidates = []
    for pid in processes.process_ids():
        try:
            if os.getpgid(pid) == child.pid:
                candidates.append(pid)
        except ProcessLookupError:
            pass
    for pid, row in processes.process_table(candidates).items():
        if row['group'] == child.pid:
            tree.known[pid] = processes.identity(row)


def running(child):
    """Observe a caller-owned, unreaped child without consuming its identity."""
    return _leader(child)['state'] != psutil.STATUS_ZOMBIE


def terminate(child):
    """Signal only the unreaped direct child; callers must not poll/wait it."""
    _leader(child)  # An unreaped direct child's PID cannot have been reassigned.
    try:
        os.kill(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def wait(child, timeout):
    """Return (exit code, expired, cleanup receipt), or fail closed.

    The caller must own a POSIX child launched with start_new_session=True and
    must not concurrently poll/wait/reap it. Inspection does not reap the root.
    """
    tree = processes.ProcessTree(child.pid, lambda rows: None)
    expired = False
    root = None
    try:
        current = _leader(child)
        root = processes.identity(current)
        if root.get('group') != child.pid:
            raise processes.ProcessError('Independent grader has no verified private process group')
        tree.known[child.pid] = root
        # Record detached descendants before the first interruptible clock read.
        tree.sample()
        deadline = time.monotonic() + timeout
        while True:
            tree.sample()
            current = _leader(child)
            if not processes.matches(root, current):
                raise processes.ProcessError('Independent grader identity disappeared before cleanup')
            if current['state'] == psutil.STATUS_ZOMBIE:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired = True
                break
            time.sleep(min(.02, remaining))
    finally:
        try:
            if root is not None:
                _capture_group(tree, child, root)
        finally:
            tree.stop(child)
    owned = list(tree.known.values())
    if processes.live_processes(owned):
        error = processes.ProcessError('Independent grader cleanup left live owned processes')
        error.processes = owned
        raise error
    return child.wait(timeout=1), expired, {'checked': True, 'owned': owned, 'live_pids': []}
