"""Independent pipe lifeline for one birth-identified provider process tree."""
from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import select
import signal
import sys
import threading
import time

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_process as processes
    import autocode_util as util


class KeeperTree(processes.ProcessTree):
    """A CLI keeper excludes itself and may stop its exact configured parent."""
    def inventory(self):
        return list(self.known.values())

    def admit(self, nested, owner):
        if (not isinstance(nested, dict) or nested.get('schema') != 1
                or not isinstance(nested.get('nonce'), str) or len(nested['nonce']) != 32
                or not processes.matches(owner, nested.get('owner'))):
            raise processes.ProcessError('Invalid nested supervision declaration')
        rows = [nested[key] for key in ('keeper', 'provider')]
        if (any(not isinstance(row, dict) or type(row.get('pid')) is not int
                or row['pid'] <= 0 or type(row.get('birth_identity')) not in (int, float)
                or not math.isfinite(row['birth_identity']) or row['birth_identity'] <= 0
                for row in rows)
                or len({row['pid'] for row in rows}) != 2):
            raise processes.ProcessError('Invalid nested supervision identities')
        table = processes.process_table({row['pid'] for row in rows})
        if any(not processes.matches(row, table.get(row['pid']))
               or table[row['pid']]['parent'] != owner['pid']
               or not self._included(row) for row in rows):
            raise processes.ProcessError('Nested supervision is not owned by this CLI')
        # Only the sampling thread admits identities, so a stale sample cannot
        # overwrite this transfer. Native sampling still handles PID reuse.
        self.known.update({row['pid']: dict(row) for row in rows})

    def signal(self, rows, sig):
        parent = [row for row in rows if row['pid'] == os.getppid() == self.pid]
        super().signal([row for row in rows if row not in parent], sig)
        for row in parent:
            current = processes.process_table({self.pid}).get(self.pid)
            if processes.matches(row, current):
                try:
                    os.kill(self.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError as error:
                    raise processes.ProcessError('Cannot signal supervised CLI owner') from error


class Observer:
    """Observe a sibling without reaping it or manufacturing its exit code."""
    def __init__(self, identity):
        self.identity = identity
        self.pid = identity['pid']

    def poll(self):
        # The return sentinel says only 'not running', never a provider result.
        return None if processes.live_processes([self.identity]) else object()

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise processes.ProcessError('Observed provider remains alive')
            time.sleep(.02)
        return None


def _leads_session(identity):
    try:
        return identity.get('group') == identity['pid'] == os.getsid(identity['pid'])
    except OSError:
        return False


def main(control, acknowledgement, external=None):
    message = bytearray()
    while len(message) <= 65536:
        if not select.select([control], [], [], 15)[0]:
            return 2
        chunk = os.read(control, 1)
        if not chunk:
            return 0  # owner died before admission; bootstrap also sees EOF
        if chunk == b'\n':
            break
        message.extend(chunk)
    else:
        return 2
    request = json.loads(message)
    metadata, deadline = request['metadata'], request['deadline']
    provider = metadata['provider']
    policy = request.get('cleanup_policy', 'freeze_tree')
    if policy not in ('freeze_tree', 'interrupt_root'):
        return 2
    if not processes.live_processes([provider]):
        return 2
    if not processes.matches(metadata['keeper'], processes.process_table({os.getpid()}).get(os.getpid())):
        return 2
    # The harness starts a guarded CLI in its own session, so the CLI's group
    # holds only its descendants that did not detach. That group stays owned
    # after the CLI is killed and reaped: a child started since the last sample
    # (a settings check, a git call) is reparented but keeps the group.
    leader = (provider,) if external is not None and _leads_session(provider) else ()
    tree = KeeperTree(provider['pid'], lambda rows: None, excluded=(metadata['keeper'],), groups=leader)
    tree.known = {provider['pid']: provider}
    receipt_write_lock = threading.Lock()
    reason_lock = threading.Lock()
    value = {**metadata, 'phase': 'armed', 'processes': list(tree.known.values()), 'cause': None,
             'cleanup_error': None, 'observed_at': util.now()}
    def save():
        # Order snapshot construction and replacement as one publication. A
        # blocked older sampling write cannot replace a later stopping receipt.
        # The state lock is released before I/O: trigger signalling/escalation
        # never waits for this disk lock or an in-progress checkpoint.
        with receipt_write_lock:
            with reason_lock:
                snapshot = {**value, 'processes': [dict(row) for row in tree.inventory()],
                            'observed_at': util.now()}
            # Never replace a readable receipt with bytes this process's reader rejects.
            if not receipts.within_reader_limit(snapshot):
                raise OSError("supervision receipt exceeds the reader limit")
            util.atomic_json(metadata['receipt'], snapshot)
    try:
        save()  # durable ownership is required before the provider can exec
    except OSError:
        return 3
    os.write(acknowledgement, (json.dumps({'armed': metadata['nonce']}) + '\n').encode())
    if external is None:
        os.close(acknowledgement)
    triggered = threading.Event()
    done = threading.Event()
    monitor_error = []
    admissions = queue.SimpleQueue()
    reason = [None]
    escalation = [None]
    def trigger(cause):
        with reason_lock:
            if triggered.is_set() or done.is_set():
                return
            reason[0] = cause
            value.update(phase='stopping', cause=cause)
            triggered.set()
        if cause == 'controller_finished' and external is not None:
            return  # A normally exiting CLI discharges without signalling itself.
        # Owner EOF and the hard deadline must not wait behind sampling or disk
        # writes. Recheck identities before signalling every recorded process.
        def signal_known(sig):
            try:
                tree.signal(tree.inventory(), sig)
            except BaseException as error:
                monitor_error.append(type(error).__name__)
        if policy == 'interrupt_root':
            try:
                tree.signal([provider], signal.SIGTERM)
            except BaseException as error:
                monitor_error.append(type(error).__name__)
        else:
            signal_known(signal.SIGSTOP)
        escalation[0] = threading.Timer(2, signal_known, args=(signal.SIGKILL,))
        escalation[0].daemon = True
        escalation[0].start()
        try:
            save()
        except OSError:
            monitor_error.append('ReceiptWriteError')
    def monitor_owner():
        pending = bytearray()
        try:
            while not done.is_set() and not triggered.is_set():
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    trigger('stage_deadline')
                    return
                readable = select.select([control, *([external] if external is not None else [])], [], [],
                                         min(.05, remaining) if remaining is not None else .05)[0]
                if not readable:
                    continue
                if external is not None and external in readable:
                    chunk = os.read(external, 1)
                    trigger('owner_lost' if not chunk else 'invalid_owner_message')
                    return
                chunk = os.read(control, 4096)
                if not chunk:
                    trigger('owner_lost')
                    return
                pending.extend(chunk)
                if len(pending) > 65536:
                    trigger('invalid_owner_message')
                    return
                while b'\n' in pending:
                    line, _, remainder = pending.partition(b'\n')
                    pending = bytearray(remainder)
                    notice = json.loads(line)
                    if (external is not None and isinstance(notice, dict)
                            and set(notice) == {'admit', 'guard'} and notice['guard'] == metadata['nonce']):
                        admissions.put(notice['admit'])
                    else:
                        trigger('controller_finished' if notice == {'finished': metadata['nonce']} else 'invalid_owner_message')
                        return
        except BaseException as error:
            monitor_error.append(type(error).__name__)
            trigger('lifeline_failure')
    watcher = threading.Thread(target=monitor_owner, daemon=True)
    watcher.start()
    result = 0
    terminal_phase = 'uncertain'
    terminal_error = None
    previous_inventory = tree.inventory()
    try:
        while not triggered.is_set():
            # Admission disk I/O belongs to the sampling thread. The lifeline
            # watcher must remain free to enforce EOF and the hard deadline.
            while not admissions.empty() and not triggered.is_set():
                nested = admissions.get_nowait()
                tree.admit(nested, provider)
                save()
                if not triggered.is_set():
                    os.write(acknowledgement,
                             (json.dumps({'admitted': nested['nonce']}) + '\n').encode())
            rows = tree.sample(notify=False)
            inventory = tree.inventory()
            if previous_inventory != inventory:
                save()
                previous_inventory = inventory
            if not rows or not processes.live_processes([provider]):
                with reason_lock:
                    if not triggered.is_set():
                        reason[0] = 'provider_stopped'
                break
            # This wait is interruptible by owner loss/deadline, not a test sleep.
            triggered.wait(.05)
        discharged = external is not None and reason[0] == 'controller_finished'
        if discharged:
            remaining = [row for row in tree.sample(notify=False) if row['pid'] != provider['pid']]
            if remaining:
                raise processes.ProcessError('CLI discharged with live child processes')
        if not discharged and policy == 'interrupt_root' and triggered.is_set():
            # Let the CLI's SIGTERM handler retain PAUSED_INTERRUPTED before
            # scoped escalation. Owner-loss qualification uses event barriers.
            until = time.monotonic() + 2
            while processes.live_processes([provider]) and time.monotonic() < until:
                done.wait(.02)
        if not discharged:
            tree.stop(Observer(provider))
        terminal_phase = 'discharged' if discharged else 'stopped'
        if monitor_error:
            terminal_error = '; '.join(monitor_error)
            result = 4
    except BaseException as error:
        terminal_error = f'{type(error).__name__}: {str(error)[:300]}'
        result = 4
        # Do not discard already recorded ownership when discovery fails.
        with contextlib.suppress(BaseException):
            tree.signal(tree.inventory(), signal.SIGKILL)
    finally:
        done.set()
        watcher.join(timeout=1)
        if watcher.is_alive():
            terminal_phase = 'uncertain'
            terminal_error = 'Lifeline monitor remains active after cleanup'
            result = 4
        # Publish the terminal phase after the monitor has stopped mutating the
        # receipt; a late normal discharge cannot overwrite stopped with stopping.
        value.update(phase=terminal_phase, cause=reason[0] or 'keeper_failure', cleanup_error=terminal_error)
        if escalation[0]:
            escalation[0].cancel()
        try:
            save()
        except OSError:
            result = 3
        os.close(control)
        if external is not None:
            os.close(acknowledgement)
            os.close(external)
    return result


if __name__ == '__main__':
    raise SystemExit(main(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else None))
