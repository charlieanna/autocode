"""POSIX provider-process supervision, including detached tool subprocesses."""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager

try:
    from . import autocode_process_children as process_children
    from . import autocode_process_receipts as process_receipts
    from . import autocode_util as util
    from .autocode_activity import idle_timeout_reason
except ImportError:
    import autocode_process_children as process_children
    import autocode_process_receipts as process_receipts
    import autocode_util as util
    from autocode_activity import idle_timeout_reason

try:
    import psutil
except ImportError as missing:  # autocode.py loads this module for --version and doctor too (#67)
    psutil = util.MissingModule("psutil", missing)


class ProcessError(RuntimeError):
    pass


def process_ids():
    last_error = None
    for attempt in range(3):
        try:
            return psutil.pids()
        except (psutil.Error, OSError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(.05 * (attempt + 1))
    raise ProcessError("Cannot enumerate provider processes; refusing an unsafe launch or cleanup") from last_error


def _birth_identity(process):
    # On macOS psutil 7's PID identity is the raw kernel epoch timestamp, unlike
    # public create_time() with its import-time clock adjustment. Linux's _ident
    # is boot-relative, so retain its epoch timestamp to distinguish reboots.
    return process._ident[1] if psutil.OSX else process.create_time()


def process_table(pids=None):
    """Read native process metadata; never inspect command arguments."""
    selected = {pid for pid in (process_ids() if pids is None else set(pids))
                if type(pid) is int and pid > 0}
    table = {}
    for pid in selected:
        try:
            process = psutil.Process(pid)
            with process.oneshot():
                born = process.create_time()
                birth_identity = _birth_identity(process)
                parent = process.ppid()
                status = process.status()
                group = os.getpgid(pid)
                try:
                    # psutil's public name() may expand a truncated POSIX name
                    # by reading cmdline(). On macOS that optional query can
                    # raise a raw SystemError when sysctl denies access. The
                    # pinned psutil 7 backend reads the native name directly;
                    # a missing name must not discard verified birth identity.
                    executable = process._proc.name()
                except (psutil.AccessDenied, OSError, SystemError, AttributeError):
                    executable = None
            table[pid] = {"pid": pid, "parent": parent, "group": group,
                          "started": " ".join(time.ctime(born).split()),
                          "birth_time": born,
                          "birth_identity": birth_identity,
                          "state": "Z" if status == psutil.STATUS_ZOMBIE else status,
                          "executable": executable}
        except (psutil.NoSuchProcess, ProcessLookupError, FileNotFoundError):
            # Gone between listing and reading: psutil can surface the vanished /proc/<pid>
            # entry as FileNotFoundError, which a live run treated as fatal (2026-10-06).
            continue
        except (psutil.AccessDenied, PermissionError) as error:
            if pids is not None:
                raise ProcessError(f"Cannot inspect owned process {pid}: access denied; workspace remains blocked") from error
        except (psutil.Error, OSError, SystemError) as error:
            raise ProcessError(f"Cannot inspect process {pid}: {type(error).__name__}; workspace remains blocked") from error
    return table


def preflight():
    """Check enumeration and native metadata without inspecting unrelated PIDs."""
    process_ids()
    pid = os.getpid()
    if pid not in process_table({pid}):
        raise ProcessError("Cannot inspect controller process; refusing an unsafe provider launch")


def identity(row):
    return {key: row[key] for key in ("pid", "started", "group", "birth_time", "birth_identity") if key in row}


def matches(saved, current):
    if not current or saved["pid"] != current["pid"]:
        return False
    if "birth_identity" in saved:
        born = saved["birth_identity"]
        return type(born) in (int, float) and born == current.get("birth_identity")
    # Old checkpoints have no stable identity; keep their stricter checks rather
    # than guessing whether a timestamp mismatch means clock drift or PID reuse.
    return (saved["started"] == current["started"]
            and ("birth_time" not in saved or saved["birth_time"] == current.get("birth_time")))


def live_processes(saved, table=None):
    table = process_table({p["pid"] for p in saved}) if table is None else table
    return [table[p["pid"]] for p in saved
            if matches(p, table.get(p["pid"])) and not table[p["pid"]]["state"].startswith("Z")]


def recorded_worker_state(record):
    """Report whether a saved attempt's recorded workers still exist.

    Read-only and never signals anything. Used by status so a checkpoint left
    behind by a dead runner or provider is not presented as current work.
    """
    try:
        saved = record.get("processes")
        if not saved:
            supervision = record.get("supervision")
            provider = supervision.get("provider") if isinstance(supervision, dict) else None
            if isinstance(provider, dict):
                pid, born = provider.get("pid"), provider.get("birth_identity")
                if type(pid) is int and pid > 0 and type(born) in (int, float) and born > 0 and math.isfinite(born):
                    saved = [provider]
        if saved:
            live = live_processes(saved)
            return {"checked": True, "alive": bool(live),
                    "live_pids": [row["pid"] for row in live]}
        # A collected direct-child exit remains authoritative for legacy records.
        # A bare PID or absent ownership cannot establish who is running.
        if type(record.get("exit_code")) is int:
            return {"checked": True, "alive": False, "live_pids": []}
    except ProcessLookupError:
        return {"checked": True, "alive": False, "live_pids": []}
    except PermissionError:
        return {"checked": False, "alive": None, "live_pids": []}
    except (ProcessError, psutil.Error, OSError, SystemError, KeyError, TypeError, ValueError, OverflowError):
        return {"checked": False, "alive": None, "live_pids": []}
    return {"checked": False, "alive": None, "live_pids": []}


class ProcessTree:
    def __init__(self, pid, checkpoint, *, excluded=(), groups=()):
        self.pid = pid
        self.known = {}
        self.checkpoint = checkpoint
        # A keeper beneath a CLI must not own itself. Exclude only the exact
        # supplied birth identity, before ancestry/group expansion.
        self.excluded = {row['pid']: dict(row) for row in excluded}
        # Recorded group leaders whose groups stay owned after the leader is
        # gone; see _retained_groups.
        self.groups = {row['pid']: dict(row) for row in groups}

    def _included(self, row):
        return row['pid'] > 0 and not matches(self.excluded.get(row['pid'], {'pid': -1}), row)

    def _retained_groups(self, table):
        """Groups of recorded leaders, still owned after a leader is killed and reaped.

        A child started after the last sample is reparented when its leader
        dies, so ancestry cannot find it, but it keeps the leader's group. The
        kernel does not reuse a PID while a process group with that ID has
        members, zombies included. Ownership of a group therefore ends for good
        at the first sample that finds it empty or finds a different process at
        the leader's PID; only after that can the ID name an unrelated group.
        A group that empties and is re-created at a reused PID between two
        samples is not detected: that needs the PID space to wrap in between.
        """
        for pid, leader in list(self.groups.items()):
            if pid in table:
                if not matches(leader, table[pid]):
                    del self.groups[pid]
                continue
            try:
                os.killpg(pid, 0)  # signal 0 delivers nothing; it reports whether the group has members
            except ProcessLookupError:
                del self.groups[pid]
            except PermissionError:
                pass  # members exist that this process may not signal
        return set(self.groups)

    def capture_root(self):
        """Record the root's birth identity before a concurrent poll can reap it.

        A fast-exiting provider can be reaped by an observer's child.poll()
        before sample() runs; an empty process receipt then blocks bounded
        startup recovery. This is only the root lookup — full descendant
        discovery still happens in sample() under live supervision.
        """
        if self.pid in self.known:
            return self.known[self.pid]
        table = process_table({self.pid})
        if self.pid not in table:
            return None
        self.known[self.pid] = identity(table[self.pid])
        self.checkpoint(list(self.known.values()))
        return self.known[self.pid]

    def sample(self, *, initial=False, notify=True):
        self.known = {pid: row for pid, row in self.known.items() if self._included(row)}
        table = process_table(set(self.known) | {self.pid} | set(self.groups))
        table = {pid: row for pid, row in table.items() if self._included(row)}
        retained = self._retained_groups(table)
        if self.pid in table and self.pid not in self.known:
            self.known[self.pid] = identity(table[self.pid])
        owned = {pid for pid, saved in self.known.items() if matches(saved, table.get(pid))}
        covered = set()
        for pid in sorted(owned, key=lambda value: value != self.pid):
            if pid in covered or table[pid]["state"].startswith("Z"):
                continue
            try:
                parent = psutil.Process(pid)
                if _birth_identity(parent) != table[pid].get("birth_identity"):
                    continue
                descendants = process_children.descendants(parent)
                candidates = {child.pid: _birth_identity(child) for child in descendants}
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError) as error:
                raise ProcessError(f"Cannot inspect descendants of owned process {pid}: {type(error).__name__}") from error
            found = process_table(candidates)
            found = {child_pid: row for child_pid, row in found.items()
                      if row["birth_identity"] == candidates[child_pid] and self._included(row)}
            table.update(found)
            owned.update(found)
            covered.update(found)
        groups = {table[pid]["group"] for pid in owned if table[pid]["group"] == pid} | retained
        if groups:
            candidates = set()
            for candidate in process_ids():
                try:
                    if candidate > 0 and os.getpgid(candidate) in groups and candidate not in table:
                        candidates.add(candidate)
                except ProcessLookupError:
                    pass
            found = process_table(candidates)
            found = {child_pid: row for child_pid, row in found.items() if row["group"] in groups and self._included(row)}
            table.update(found)
            owned.update(found)
        while True:
            # A group is owned only while a recorded group leader has the same
            # process identity, or is a retained group not yet seen empty or
            # taken over at its leader's PID (_retained_groups). This avoids
            # signalling an unrelated reused PID.
            groups = {table[pid]["group"] for pid in owned if table[pid]["group"] == pid} | retained
            added = {pid for pid, row in table.items()
                     if row["parent"] in owned or row["group"] in groups} - owned
            if not added:
                break
            owned.update(added)
        updated = {**self.known, **{pid: identity(table[pid]) for pid in owned}}
        changed = updated != self.known
        self.known = updated
        if notify and (initial or changed):
            self.checkpoint(list(self.known.values()))
        return live_processes(list(self.known.values()), table)

    def signal(self, rows, sig):
        # Recheck birth identity immediately before a signalling batch.
        table = process_table({row["pid"] for row in rows if type(row['pid']) is int and row['pid'] > 0})
        for row in rows:
            if row["pid"] <= 0 or row["pid"] in (os.getpid(), os.getppid()) or not matches(row, table.get(row["pid"])):
                continue
            try:
                os.kill(row["pid"], sig)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                # A denied signal on a birth-verified owned process leaves cleanup
                # uncertain; fail closed with the typed error so no partial outcome
                # is published. The retired killpg path swallowed this class.
                raise ProcessError(f"Cannot signal owned process {row['pid']}: permission denied") from error

    def stop(self, child):
        # Freeze the verified tree before termination. Always resume anything
        # we may have stopped, even when an ancestry scan or signal fails.
        frozen = {}
        try:
            for _ in range(8):
                rows = self.sample(notify=False)
                fresh = [r for r in rows if (r["pid"], r["started"]) not in frozen]
                if not fresh:
                    break
                frozen.update({(r["pid"], r["started"]): r for r in fresh})
                self.signal(fresh, signal.SIGSTOP)
            rows = self.sample(notify=False)
            self.signal(rows, signal.SIGTERM)
            self.signal(rows, signal.SIGCONT)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                child.poll()
                rows = self.sample(notify=False)
                if not rows:
                    if child.poll() is None:
                        child.wait(timeout=1)
                    return
                time.sleep(.05)
            self.signal(rows, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                child.poll()
                if not self.sample(notify=False):
                    if child.poll() is None:
                        child.wait(timeout=1)
                    return
                time.sleep(.05)
            raise ProcessError("Provider commands remain alive after cleanup; the workspace remains blocked")
        except ProcessError:
            # A failed discovery pass must not strand recorded workers stopped.
            self.signal(list(self.known.values()), signal.SIGKILL)
            raise
        finally:
            self.signal(list(frozen.values()), signal.SIGCONT)


# A stage's cleanup defers every signal interruption_handler turns into an interrupt.
INTERRUPTS = tuple(getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name))
# The open interrupts_held scope, if any: the dispositions an interrupted stage replaced,
# and the signals absorbed after its interrupt.
_held = []
# The open interruption_handler scope, if any. A scope nested in it shares its one interrupt.
_handling = []


def _callers_own(handler):
    """A handler the caller installed, as opposed to a default or ignored disposition."""
    return callable(handler) and handler is not signal.default_int_handler


@contextmanager
def interrupts_held(*, until_exit=False):
    """Keep an interrupted stage's later signals absorbed until this scope ends.

    The stage's handler scope closes before its caller saves the pause and the CLI
    exits. A second signal there escaped as a bare KeyboardInterrupt or killed the
    controller by default, often leaving the run RUNNING (#454). Wrap one CLI
    invocation in it; a scope nested in another defers to that one. When it ends a
    caller's own handler comes back and receives each signal absorbed meanwhile, once.
    A default disposition comes back too, except with until_exit, for the CLI process
    that ends with this scope: the signal then stays ignored, so a late Ctrl-C or
    hangup cannot turn the saved pause into a death by signal.
    """
    if _held:
        yield
        return
    held = {'replaced': {}, 'absorbed': []}
    _held.append(held)
    try:
        yield
    finally:
        _held.pop()
        for sig, handler in held['replaced'].items():
            signal.signal(sig, handler if _callers_own(handler) or not until_exit else signal.SIG_IGN)
        for sig in dict.fromkeys(held['absorbed']):
            if _callers_own(held['replaced'].get(sig)):
                signal.raise_signal(sig)


@contextmanager
def interruption_handler():
    """Turn SIGTERM, SIGHUP and Ctrl-C into one KeyboardInterrupt naming the signal.

    Only the first raises. A closed terminal sends SIGHUP twice (the kernel and the
    shell) and people press Ctrl-C again; a second raise while the first unwound
    skipped cleanup, misreported the pause or hung the controller in a leaked
    threading lock (#454). Inside interrupts_held, later signals stay absorbed
    after this scope too, until the held scope ends. A scope opened inside another
    on the main thread changes nothing: the enclosing one raises the one interrupt.
    """
    if _handling and threading.current_thread() is threading.main_thread():
        yield
        return
    held = _held[-1] if _held else None

    def before(sig):  # the disposition before a stage of this invocation was interrupted
        return (held['replaced'] if held else {}).get(sig, signal.getsignal(sig))
    signals = [signal.SIGTERM]
    # A terminal hangup follows the same retained interrupt path. Respect nohup
    # and callers that explicitly inherited SIGHUP ignored.
    if hasattr(signal, 'SIGHUP') and before(signal.SIGHUP) != signal.SIG_IGN:
        signals.append(signal.SIGHUP)
    # Likewise leave an ignored (background job) or caller-installed SIGINT alone.
    if before(signal.SIGINT) is signal.default_int_handler:
        signals.append(signal.SIGINT)
    raised = []

    def interrupt(signum, frame):
        if raised:
            if held is not None:
                held['absorbed'].append(signum)
            return  # the first interrupt's cleanup is under way
        raised.append(signum)
        raise KeyboardInterrupt(signal.Signals(signum).name)
    previous = {sig: signal.signal(sig, interrupt) for sig in signals}
    _handling.append(raised)
    try:
        yield
    finally:
        _handling.pop()
        if raised and held is not None:
            for sig, handler in previous.items():
                held['replaced'].setdefault(sig, handler)  # this scope's handler goes on absorbing
        else:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def wait_for_stage(child, timeout, checkpoint, *, activity=None, activity_checkpoint=None,
                   startup_grace=0):
    receipts = process_receipts.ReceiptWorker()
    tree = ProcessTree(child.pid, receipts.record)
    deadline = time.monotonic() + timeout if timeout else None
    startup_deadline = time.monotonic() + max(0, float(startup_grace))
    stopped = threading.Event()
    watchdog_fired = threading.Event()
    root_captured = threading.Event()
    firing = threading.Lock()
    # Process ownership is maintained independently of controller persistence.
    # Only this calling thread invokes checkpoint callbacks; the worker owns
    # discovery/cleanup and publishes whole receipts, even while a save stalls.
    live = []
    latest_activity = None
    latest_observation = (time.monotonic(), {
        "idle_seconds": 0, "tool_elapsed_seconds": None,
        "idle_limit_seconds": getattr(activity, "idle_limit", 0),
        "tool_limit_seconds": getattr(activity, "tool_limit", 0)})
    watchdog_errors = []
    last_publish = 0
    last_activity_key = None
    escalation_timer = None

    def signal_provider(sig):
        if child.poll() is not None:
            return
        try:
            # Providers are launched with start_new_session=True, so their pid
            # is a private process-group leader.  Recheck that invariant before
            # signalling a group; a mock or non-conforming child only receives
            # a direct signal.
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, sig)
            else:
                os.kill(child.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def stop_at_deadline(reason):
        nonlocal latest_activity, escalation_timer
        with firing:
            if stopped.is_set() or watchdog_fired.is_set() or child.poll() is not None:
                return
            if activity is not None:
                activity.timeout = reason
                # Never acquire the monitor's lock on the termination path: a
                # blocked log read must not prevent the independent hard cap.
                latest_activity = {**(latest_activity or {}),
                                   "activity": "stalled" if reason["kind"] == "idle" else "timed_out",
                                   "timeout_kind": reason["kind"], "timeout_reason": reason["reason"]}
            watchdog_fired.set()
        escalation_timer = threading.Timer(2, signal_provider, args=(signal.SIGKILL,))
        escalation_timer.daemon = True
        escalation_timer.start()
        signal_provider(signal.SIGTERM)

    def observe():
        nonlocal latest_activity, latest_observation
        try:
            while not stopped.is_set() and not watchdog_fired.is_set() and child.poll() is None:
                observed_at = time.monotonic()
                observed = activity.poll(processes=live, root_pid=child.pid)
                with firing:
                    if stopped.is_set() or watchdog_fired.is_set():
                        return
                    latest_activity = observed
                    latest_observation = (observed_at, observed)
                if stopped.wait(.05):
                    return
        except Exception as error:
            # A monitor failure cannot leave a worker running without supervision.
            watchdog_errors.append(error)
            stop_at_deadline({"kind": "monitor", "reason": "Activity supervision failed"})

    def supervise():
        while not stopped.is_set() and not watchdog_fired.is_set():
            # Routine polling must not reap a fast exit before identity capture.
            # Deadline termination remains active while inspection or writes stall.
            if root_captured.is_set() and child.poll() is not None:
                return
            current = time.monotonic()
            if deadline is not None and current >= deadline:
                stop_at_deadline({"kind": "stage", "reason": f"Stage exceeded its {timeout:g}-second hard runtime limit"})
                return
            if current < startup_deadline:
                if stopped.wait(.05):
                    return
                continue
            observed_at, snapshot = latest_observation
            lag = max(0, current - observed_at)
            tool_elapsed = snapshot.get("tool_elapsed_seconds")
            kind = "tool" if tool_elapsed is not None else "idle"
            elapsed = tool_elapsed if kind == "tool" else snapshot.get("idle_seconds", 0)
            limit = snapshot.get(kind + "_limit_seconds", 0)
            if limit and elapsed + lag >= limit:
                reason = (f"Tool execution exceeded its fixed time limit ({limit:g} seconds)" if kind == "tool"
                          else getattr(activity, "idle_reason", idle_timeout_reason)(limit))
                stop_at_deadline({"kind": kind, "reason": reason})
                return
            if stopped.wait(.05):
                return

    def publish_activity(*, force=False):
        nonlocal last_publish, last_activity_key
        snapshot = latest_activity
        if snapshot is None or activity_checkpoint is None:
            return
        key = (snapshot.get("activity"), snapshot.get("detail"), snapshot.get("timeout_kind"))
        current = time.monotonic()
        if force or key != last_activity_key or current - last_publish >= 1:
            activity_checkpoint(dict(snapshot))
            last_publish, last_activity_key = current, key

    def cleanup_owned():
        try:
            tree.stop(child)
        except ProcessError as error:
            error.processes = list(tree.known.values())
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
            raise
        finally:
            if tree.known:
                receipts.record(list(tree.known.values()))

    def own_processes():
        nonlocal live
        try:
            live = tree.sample(initial=True)
            while not receipts.cancel.is_set() and child.poll() is None:
                live = tree.sample()
                if watchdog_fired.is_set():
                    break
                try:
                    child.wait(timeout=min(.2, max(.001, deadline - time.monotonic())) if deadline else .2)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            # Includes normal exits. A blocked controller save cannot defer
            # stopping detached writers beyond the supervision result.
            cleanup_owned()

    # The hard limit never depends on event parsing, process sampling or writes.
    hard_timer = threading.Timer(timeout, stop_at_deadline, args=({
        "kind": "stage", "reason": f"Stage exceeded its {timeout:g}-second hard runtime limit"},)) if timeout else None
    watchdog = threading.Thread(target=supervise, daemon=True) if activity is not None else None
    observer = threading.Thread(target=observe, daemon=True) if activity is not None else None
    try:
        if hard_timer:
            hard_timer.daemon = True
            hard_timer.start()
        if watchdog:
            watchdog.start()
        # Capture before ordinary observer/watchdog polling, after independent
        # deadline enforcement starts. This publishes in memory, without I/O.
        tree.capture_root()
        root_captured.set()
        if observer:
            observer.start()
        receipts.start(own_processes)
        while not receipts.done.is_set():
            receipts.flush(checkpoint)
            publish_activity()
            receipts.done.wait(.05)
        receipts.flush(checkpoint)
        publish_activity(force=True)
    finally:
        # Interruption or a failed save must not let the process worker escape
        # this call. Callbacks stay serialized on the controller thread. A signal
        # meanwhile is deferred, not ignored: ignoring lost the hangup that came
        # as a provider ended, and the run went on (#454).
        deferred = []
        handlers = {sig: signal.signal(sig, lambda signum, frame: deferred.append(signum)) for sig in INTERRUPTS}
        try:
            receipts.cancel.set()
            if receipts.started:
                receipts.join()
            else:
                cleanup_owned()
            if receipts.error is not None:
                raise receipts.error
            # An interrupt can precede the controller's next flush. Preserve
            # all discovered identities before propagating it, but do not
            # replay an already failed write or mask uncertain cleanup.
            if not receipts.checkpoint_failed:
                receipts.flush(checkpoint)
        finally:
            stopped.set()
            if hard_timer:
                hard_timer.cancel()
            if watchdog and watchdog.is_alive():
                watchdog.join(timeout=1)
            if observer and observer.is_alive():
                observer.join(timeout=1)
            if escalation_timer:
                escalation_timer.cancel()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    if watchdog_errors:
        raise ProcessError("Activity supervision failed; tracked workers have been stopped") from watchdog_errors[0]
    result = child.wait(timeout=1), watchdog_fired.is_set()
    # Only now, with the provider collected, does a deferred signal reach the caller's
    # own handler (the stage's interrupt, or an ignore under nohup). Behind an error
    # already propagating it is dropped: that error ends the stage.
    for sig in dict.fromkeys(deferred):
        signal.raise_signal(sig)
    return result
