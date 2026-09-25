"""POSIX provider-process supervision, including detached tool subprocesses."""
from __future__ import annotations

from contextlib import contextmanager
import os
import signal
import subprocess
import threading
import time

import psutil


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


def process_table(pids=None):
    """Read native process metadata; never inspect command arguments."""
    selected = process_ids() if pids is None else set(pids)
    table = {}
    for pid in selected:
        try:
            process = psutil.Process(pid)
            with process.oneshot():
                born = process.create_time()
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
                          "state": "Z" if status == psutil.STATUS_ZOMBIE else status,
                          "executable": executable}
        except (psutil.NoSuchProcess, ProcessLookupError):
            continue
        except (psutil.AccessDenied, PermissionError) as error:
            if pids is not None:
                raise ProcessError(f"Cannot inspect owned process {pid}: access denied; workspace remains blocked") from error
        except (psutil.Error, OSError, SystemError) as error:
            raise ProcessError(f"Cannot inspect process {pid}: {type(error).__name__}; workspace remains blocked") from error
    return table


def identity(row):
    return {key: row[key] for key in ("pid", "started", "group", "birth_time") if key in row}


def matches(saved, current):
    if not current or saved["pid"] != current["pid"] or saved["started"] != current["started"]:
        return False
    return "birth_time" not in saved or saved["birth_time"] == current.get("birth_time")


def live_processes(saved, table=None):
    table = process_table({p["pid"] for p in saved}) if table is None else table
    return [table[p["pid"]] for p in saved
            if matches(p, table.get(p["pid"])) and not table[p["pid"]]["state"].startswith("Z")]


class ProcessTree:
    def __init__(self, pid, checkpoint):
        self.pid = pid
        self.known = {}
        self.checkpoint = checkpoint

    def sample(self, *, initial=False, notify=True):
        table = process_table(set(self.known) | {self.pid})
        if self.pid in table and self.pid not in self.known:
            self.known[self.pid] = identity(table[self.pid])
        owned = {pid for pid, saved in self.known.items() if matches(saved, table.get(pid))}
        covered = set()
        for pid in sorted(owned, key=lambda value: value != self.pid):
            if pid in covered or table[pid]["state"].startswith("Z"):
                continue
            try:
                parent = psutil.Process(pid)
                if parent.create_time() != table[pid].get("birth_time"):
                    continue
                descendants = parent.children(recursive=True)
                candidates = {child.pid: child.create_time() for child in descendants}
            except psutil.NoSuchProcess:
                continue
            except PermissionError:
                # macOS can deny sysctl's process-list allocation under table
                # pressure. Recover ancestry with scoped ppid reads; do not
                # abandon the descendants of an already verified provider.
                candidates = {}
                for candidate in process_ids():
                    try:
                        child = psutil.Process(candidate)
                        if child.ppid() == pid:
                            candidates[candidate] = child.create_time()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                found = process_table(candidates)
                found = {child_pid: row for child_pid, row in found.items()
                         if row["birth_time"] == candidates[child_pid]}
                table.update(found)
                owned.update(found)
                covered.update(found)
            except psutil.Error as error:
                raise ProcessError(f"Cannot inspect descendants of owned process {pid}: {type(error).__name__}") from error
            found = process_table(candidates)
            found = {child_pid: row for child_pid, row in found.items()
                     if row["birth_time"] == candidates[child_pid]}
            table.update(found)
            owned.update(found)
            covered.update(found)
        groups = {table[pid]["group"] for pid in owned if table[pid]["group"] == pid}
        if groups:
            candidates = set()
            for candidate in process_ids():
                try:
                    if os.getpgid(candidate) in groups and candidate not in table:
                        candidates.add(candidate)
                except ProcessLookupError:
                    pass
            found = process_table(candidates)
            found = {child_pid: row for child_pid, row in found.items() if row["group"] in groups}
            table.update(found)
            owned.update(found)
        while True:
            # A group is owned only while a recorded group leader has the same
            # process identity. This avoids signalling an unrelated reused PID.
            groups = {table[pid]["group"] for pid in owned if table[pid]["group"] == pid}
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
        table = process_table({row["pid"] for row in rows})
        for row in rows:
            if row["pid"] in (os.getpid(), os.getppid()) or not matches(row, table.get(row["pid"])):
                continue
            try:
                os.kill(row["pid"], sig)
            except ProcessLookupError:
                pass

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


@contextmanager
def interruption_handler():
    previous = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def wait_for_stage(child, timeout, checkpoint, *, activity=None, activity_checkpoint=None,
                   startup_grace=0):
    tree = ProcessTree(child.pid, checkpoint)
    deadline = time.monotonic() + timeout if timeout else None
    startup_deadline = time.monotonic() + max(0, float(startup_grace))
    stopped = threading.Event()
    watchdog_fired = threading.Event()
    firing = threading.Lock()
    # Only the owner thread samples processes and persists state. The independent
    # event reader consumes bounded chunks; deadline enforcement never waits on
    # that I/O, ps, or a checkpoint write. Assign whole snapshots across threads.
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
        while not stopped.is_set() and not watchdog_fired.is_set() and child.poll() is None:
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
                description = "Tool execution exceeded its fixed time limit" if kind == "tool" else "No new provider activity within the inactivity limit"
                stop_at_deadline({"kind": kind, "reason": f"{description} ({limit:g} seconds)"})
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

    # The hard limit never depends on event parsing, process sampling or writes.
    hard_timer = threading.Timer(timeout, stop_at_deadline, args=({
        "kind": "stage", "reason": f"Stage exceeded its {timeout:g}-second hard runtime limit"},)) if timeout else None
    if hard_timer:
        hard_timer.daemon = True
        hard_timer.start()
    watchdog = threading.Thread(target=supervise, daemon=True) if activity is not None else None
    observer = threading.Thread(target=observe, daemon=True) if activity is not None else None
    if observer:
        observer.start()
    if watchdog:
        watchdog.start()
    try:
        live = tree.sample(initial=True)
        publish_activity()
        while child.poll() is None:
            live = tree.sample()
            publish_activity()
            if watchdog_fired.is_set():
                break
            try:
                child.wait(timeout=min(.2, max(.001, deadline - time.monotonic())) if deadline else .2)
            except subprocess.TimeoutExpired:
                pass
    finally:
        stopped.set()
        if hard_timer:
            hard_timer.cancel()
        if watchdog:
            watchdog.join(timeout=1)
        if observer:
            observer.join(timeout=1)
        # This includes normal exits: a bounded stage must not leave background
        # writers running after its final source snapshot or workspace unlock.
        handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                try:
                    publish_activity(force=True)
                finally:
                    tree.stop(child)
            except ProcessError as error:
                error.processes = list(tree.known.values())
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=2)
                raise
        finally:
            if escalation_timer:
                escalation_timer.cancel()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    if watchdog_errors:
        raise ProcessError("Activity supervision failed; tracked workers have been stopped") from watchdog_errors[0]
    return child.wait(timeout=1), watchdog_fired.is_set()
