"""A provider stays bounded when its controller disappears.

The controller keeps its actual Popen child. A blocked exec bootstrap closes the
launch-before-ownership gap; a separate-session sibling keeper owns no provider
streams or checkout lock. This module writes only its caller-supplied receipt,
never run state. The caller persists metadata before provider release and closes
this context after its existing process supervisor has cleaned the provider.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
import uuid

try:
    from . import autocode_process as processes, autocode_util as util, autocode_supervision_handoff as handoff
except ImportError:
    import autocode_process as processes
    import autocode_util as util
    import autocode_supervision_handoff as handoff


class SupervisionError(processes.ProcessError):
    pass


def receipt(metadata):
    """Read a bounded receipt bound to one launch; missing/invalid is unknown."""
    try:
        path = Path(metadata['receipt'])
        if path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get('schema') != 1:
            return None
        for key in ('nonce', 'owner', 'keeper', 'provider'):
            if value.get(key) != metadata.get(key):
                return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def observe(metadata):
    """Fresh, read-only owner/keeper/provider liveness; denied access is unknown."""
    result = {'receipt': receipt(metadata), 'owner': None, 'keeper': None, 'provider': None}
    for key in ('owner', 'keeper', 'provider'):
        saved = metadata.get(key) if isinstance(metadata, dict) else None
        if not isinstance(saved, dict) or 'birth_identity' not in saved:
            continue
        try:
            result[key] = {'checked': True, 'alive': bool(processes.live_processes([saved]))}
        except (processes.ProcessError, OSError, ValueError, KeyError, TypeError):
            result[key] = {'checked': False, 'alive': None}
    return result


def _identity(pid):
    row = processes.process_table({pid}).get(pid)
    if not row or row['state'].startswith('Z'):
        raise SupervisionError('Cannot capture supervision process birth identity')
    return processes.identity(row)


def _read_line(fd, deadline):
    data = bytearray()
    while len(data) <= 65536:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise SupervisionError('Keeper did not arm within the launch limit')
        chunk = os.read(fd, 1)
        if not chunk:
            raise SupervisionError('Keeper exited before acknowledging ownership')
        if chunk == b'\n':
            return json.loads(data)
        data.extend(chunk)
    raise SupervisionError('Keeper acknowledgement exceeded its limit')


def _send(fd, value):
    data = (json.dumps(value, separators=(',', ':')) + '\n').encode()
    while data:
        data = data[os.write(fd, data):]


def _stop_direct(child):
    if child is None:
        return
    if child.poll() is None:
        try:
            child.kill()
        except ProcessLookupError:
            pass
    child.wait(timeout=5)


@contextmanager
def launch(command, *, receipt_path, timeout=None, checkpoint=None, cleanup_policy='freeze_tree', **options):
    """Yield the actual provider Popen child after durable keeper admission.

    ``checkpoint(child.supervision)`` runs before exec. It must durably attach
    these identities to the already admitted attempt. It may raise: the provider
    is then never released. The receipt carries observed cleanup, not an exit
    code; the existing caller remains the child's sole waitpid authority.
    """
    if not isinstance(command, (list, tuple)) or not command or any(not isinstance(s, str) for s in command):
        raise SupervisionError('Supervision requires a nonempty argv')
    if options.get('shell') or options.get('executable') or options.get('preexec_fn'):
        raise SupervisionError('Supervision requires a direct, thread-safe argv launch')
    if options.get('start_new_session') is not True:
        raise SupervisionError('Supervised provider must have its own session')
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise SupervisionError('Supervision timeout must be positive or disabled')
    if cleanup_policy not in ('freeze_tree', 'interrupt_root'):
        raise SupervisionError('Unknown supervision cleanup policy')
    path = Path(receipt_path).resolve()
    if path.exists():
        raise SupervisionError('Supervision receipt already exists; retain the previous attempt')
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = _identity(os.getpid())
    control_r, control_w = os.pipe()
    ack_r, ack_w = os.pipe()
    release_r, release_w = os.pipe()
    owned_fds = {control_r, control_w, ack_r, ack_w, release_r, release_w}
    keeper = child = None
    metadata = None
    stopped = threading.Event()
    errors = []
    monitor = None
    started = time.monotonic()
    def close_fd(fd):
        if fd in owned_fds:
            owned_fds.remove(fd)
            os.close(fd)
    try:
        keeper = subprocess.Popen([sys.executable, '-E', str(Path(__file__).with_name('autocode_supervision_keeper.py')),
                                   str(control_r), str(ack_w)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True, close_fds=True, pass_fds=(control_r, ack_w))
        close_fd(control_r)
        close_fd(ack_w)
        bootstrap = [sys.executable, '-I', str(Path(__file__).with_name('autocode_supervision_bootstrap.py')),
                     str(release_r), json.dumps(list(command))]
        child_options = {**options, 'close_fds': True,
                         'pass_fds': tuple(dict.fromkeys((*options.get('pass_fds', ()), release_r)))}
        child = subprocess.Popen(bootstrap, **child_options)
        close_fd(release_r)
        metadata = {'schema': 1, 'nonce': uuid.uuid4().hex, 'owner': owner,
                    'keeper': _identity(keeper.pid), 'provider': _identity(child.pid),
                    'receipt': str(path), 'started_at': util.now()}
        child.supervision = metadata
        child.supervision_errors = errors
        _send(control_w, {'metadata': metadata,
                          'deadline': started + timeout if timeout else None, 'cleanup_policy': cleanup_policy})
        ack = _read_line(ack_r, min(started + 15, started + timeout) if timeout else started + 15)
        if ack != {'armed': metadata['nonce']} or keeper.poll() is not None:
            raise SupervisionError('Keeper refused provider ownership')
        current = receipt(metadata)
        if current is None or current.get('phase') != 'armed':
            raise SupervisionError('Keeper ownership receipt is missing or invalid')
        close_fd(ack_r)
        # A dead keeper must not strand a provider even while the controller's
        # checkpoint callback is blocked. No runner state is touched here.
        def watch_keeper():
            while not stopped.wait(.05):
                code = keeper.poll()
                if code is not None:
                    value = receipt(metadata)
                    if code != 0 or value is None or value.get('phase') != 'stopped':
                        errors.append('Independent keeper stopped without verified cleanup')
                        try:
                            try:
                                from .autocode_supervision_keeper import Observer
                            except ImportError:
                                from autocode_supervision_keeper import Observer
                            tree = processes.ProcessTree(child.pid, lambda rows: None)
                            tree.known = {child.pid: metadata['provider']}
                            if value:
                                tree.known.update({row['pid']: row for row in value.get('processes', [])})
                            tree.stop(Observer(metadata['provider']))
                        except BaseException as error:
                            errors.append(f'Keeper-loss cleanup uncertain: {type(error).__name__}')
                    return
        monitor = threading.Thread(target=watch_keeper, daemon=True)
        monitor.start()
        # Transfer nested ownership before exec while the bootstrap is blocked.
        handoff.admit(metadata, min(started + 15, started + timeout) if timeout else started + 15)
        if checkpoint is not None:
            checkpoint(dict(metadata))
        if (keeper.poll() is not None or child.poll() is not None
                or not processes.live_processes([metadata['keeper']])
                or (receipt(metadata) or {}).get('phase') != 'armed'):
            raise SupervisionError('Supervision stopped during the admission checkpoint')
        os.write(release_w, b'G')
        close_fd(release_w)
        yield child
        # The caller has now reaped and cleaned its provider tree. Discharge
        # only on that boundary; abnormal context exits use EOF cleanup instead.
        try:
            _send(control_w, {'finished': metadata['nonce']})
        except BrokenPipeError:
            # A fast normal provider may already have been cleaned and its
            # keeper exited. The final receipt and exit status below decide.
            pass
        close_fd(control_w)
        try:
            keeper.wait(timeout=8)
        except subprocess.TimeoutExpired as error:
            raise SupervisionError('Keeper cleanup did not finish within its bound') from error
        final = receipt(metadata)
        child.supervision_result = final
        if errors or keeper.returncode != 0 or final is None or final.get('phase') != 'stopped' or final.get('cleanup_error'):
            raise SupervisionError('; '.join(errors) or 'Keeper cleanup receipt is incomplete')
        if final.get('cause') not in ('provider_stopped', 'controller_finished'):
            raise SupervisionError(f"Provider supervision interrupted: {final.get('cause')}")
    finally:
        # Closing the only writer is authoritative owner loss. The bootstrap
        # also sees EOF if setup failed before provider release.
        for fd in tuple(owned_fds):
            close_fd(fd)
        if keeper is not None:
            try:
                keeper.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _stop_direct(keeper)
        stopped.set()
        if monitor is not None:
            monitor.join(timeout=5)
        if child is not None and child.poll() is None:
            # A keeper launch/setup failure still owns an unreaped direct child.
            _stop_direct(child)


@contextmanager
def protect_owner(lifeline_fd, *, receipt_path, timeout, owner_identity, nonce, checkpoint=None):
    """The public CLI guards itself against an inherited harness-only lifeline.

    The CLI has already consumed the bounded launch declaration. Its keeper
    receives the inherited read end; neither CLI nor keeper owns a writer.
    A normal exit discharges this guard after provider contexts have closed.
    ``timeout=None`` guards without a deadline (a Builder worker's lifeline).
    """
    if not isinstance(owner_identity, dict) or 'birth_identity' not in owner_identity:
        raise SupervisionError('Harness owner has no stable birth identity')
    if not processes.live_processes([owner_identity]):
        raise SupervisionError('Harness owner is gone before CLI admission')
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise SupervisionError('Harness launch nonce is invalid')
    if timeout is not None and timeout <= 0:
        raise SupervisionError('Harness launch deadline has expired')
    path = Path(receipt_path).resolve()
    if path.exists():
        raise SupervisionError('Harness supervision receipt already exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    control_r, control_w = os.pipe()
    ack_r, ack_w = os.pipe()
    fds = {control_r, control_w, ack_r, ack_w}
    keeper = None
    metadata = None
    stopped = threading.Event()
    watcher = None
    errors = []
    def close(fd):
        if fd in fds:
            fds.remove(fd)
            os.close(fd)
    try:
        keeper = subprocess.Popen([sys.executable, '-E', str(Path(__file__).with_name('autocode_supervision_keeper.py')),
                                   str(control_r), str(ack_w), str(lifeline_fd)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True, close_fds=True, pass_fds=(control_r, ack_w, lifeline_fd))
        close(control_r)
        close(ack_w)
        metadata = {'schema': 1, 'nonce': nonce, 'owner': dict(owner_identity),
                    'keeper': _identity(keeper.pid), 'provider': _identity(os.getpid()),
                    'receipt': str(path), 'started_at': util.now()}
        owner_deadline = time.monotonic() + timeout if timeout is not None else None
        _send(control_w, {'metadata': metadata, 'deadline': owner_deadline,
                          'cleanup_policy': 'interrupt_root'})
        ack = _read_line(ack_r, time.monotonic() + (min(15, timeout) if timeout is not None else 15))
        if ack != {'armed': nonce} or keeper.poll() is not None or receipt(metadata) is None:
            raise SupervisionError('Harness keeper failed admission')
        if select.select([lifeline_fd], [], [], 0)[0]:
            raise SupervisionError('Harness lifeline closed before CLI admission')
        def watch():
            while not stopped.wait(.05):
                code = keeper.poll()
                if code is not None:
                    current = receipt(metadata)
                    if code != 0 or current is None or current.get('phase') not in ('stopped', 'discharged'):
                        errors.append('Harness keeper stopped without verified cleanup')
                        os.kill(os.getpid(), signal.SIGTERM)
                    return
        import signal
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        if checkpoint is not None:
            checkpoint(dict(metadata))
        channel_lock = threading.Lock()
        def admit_nested(nested, deadline):
            # Serialize framed requests and acknowledgements across launch threads.
            with channel_lock:
                if keeper.poll() is not None or stopped.is_set():
                    raise SupervisionError('Enclosing keeper is unavailable')
                _send(control_w, {'admit': nested, 'guard': nonce})
                answer = _read_line(ack_r, deadline if owner_deadline is None else min(deadline, owner_deadline))
                current = receipt(metadata)
                inventory = (current or {}).get('processes', [])
                if (answer != {'admitted': nested['nonce']} or current is None
                        or current.get('phase') != 'armed'
                        or any(not any(processes.matches(nested[key], row) for row in inventory)
                               for key in ('provider', 'keeper'))):
                    raise SupervisionError('Enclosing keeper did not retain nested ownership')
        try:
            with handoff.enclosing_guard(admit_nested):
                yield metadata
        finally:
            current = receipt(metadata)
            # EOF cleanup must not wait for itself: the CLI has to return from
            # this context before its keeper can observe/reap its disappearance.
            readable = select.select([lifeline_fd], [], [], 0)[0]
            interrupted = bool(readable) or (current is not None and current.get('cause') not in
                          (None, 'provider_stopped', 'controller_finished'))
            if not interrupted:
                try:
                    _send(control_w, {'finished': nonce})
                except BrokenPipeError as error:
                    raise SupervisionError('Harness keeper exited before discharge') from error
                close(control_w)
                keeper.wait(timeout=8)
                current = receipt(metadata)
                if errors or keeper.returncode != 0 or current is None or current.get('phase') != 'discharged':
                    raise SupervisionError('; '.join(errors) or 'Harness keeper cleanup was uncertain')
    finally:
        for fd in tuple(fds):
            close(fd)
        stopped.set()
        if watcher is not None:
            watcher.join(timeout=1)
        # An armed keeper owns the surviving read end and remains responsible
        # after this CLI exits. Waiting here would deadlock interrupted teardown.
        if keeper is not None and metadata is None:
            try:
                keeper.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _stop_direct(keeper)
