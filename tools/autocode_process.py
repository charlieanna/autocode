"""POSIX provider-process supervision, including detached tool subprocesses."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import threading
import time


class ProcessError(RuntimeError):
    pass


def process_table():
    """Read identity, ancestry and executable names; never command arguments."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,lstart=,stat=,comm="], capture_output=True,
            text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProcessError("Cannot inspect provider subprocesses; refusing an unsafe launch or cleanup") from error
    if result.returncode:
        raise ProcessError("Cannot inspect provider subprocesses; refusing an unsafe launch or cleanup")
    table = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10:
            raise ProcessError("Unexpected process metadata format")
        pid, parent, group = map(int, fields[:3])
        table[pid] = {"pid": pid, "parent": parent, "group": group,
                      "started": " ".join(fields[3:8]), "state": fields[8],
                      "executable": Path(fields[9]).name}
    return table


def identity(row):
    return {key: row[key] for key in ("pid", "started", "group")}


def matches(saved, current):
    return bool(current and saved["pid"] == current["pid"] and saved["started"] == current["started"])


def live_processes(saved, table=None):
    table = process_table() if table is None else table
    return [table[p["pid"]] for p in saved
            if matches(p, table.get(p["pid"])) and not table[p["pid"]]["state"].startswith("Z")]


class ProcessTree:
    def __init__(self, pid, checkpoint):
        self.pid = pid
        self.known = {}
        self.checkpoint = checkpoint

    def sample(self, *, initial=False, notify=True):
        table = process_table()
        if initial and self.pid in table:
            self.known[self.pid] = identity(table[self.pid])
        owned = {pid for pid, saved in self.known.items() if matches(saved, table.get(pid))}
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
        table = process_table()
        for row in rows:
            if row["pid"] in (os.getpid(), os.getppid()) or not matches(row, table.get(row["pid"])):
                continue
            try:
                os.kill(row["pid"], sig)
            except ProcessLookupError:
                pass

    def stop(self, child):
        # Freeze parents and newly discovered descendants before termination so a
        # tool cannot be orphaned in the interval between discovery and signalling.
        frozen = set()
        for _ in range(8):
            rows = self.sample(notify=False)
            fresh = [r for r in rows if (r["pid"], r["started"]) not in frozen]
            if not fresh:
                break
            self.signal(fresh, signal.SIGSTOP)
            frozen.update((r["pid"], r["started"]) for r in fresh)
        rows = self.sample(notify=False)
        self.signal(rows, signal.SIGTERM)
        self.signal(rows, signal.SIGCONT)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            child.poll()  # reap the direct child; orphan zombies are not writers
            rows = self.sample(notify=False)
            if not rows:
                return
            time.sleep(.05)
        self.signal(rows, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            child.poll()
            if not self.sample(notify=False):
                return
            time.sleep(.05)
        raise ProcessError("Provider commands remain alive after cleanup; the workspace remains blocked")


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


def wait_for_stage(child, timeout, checkpoint, *, activity=None, activity_checkpoint=None):
    tree = ProcessTree(child.pid, checkpoint)
    deadline = time.monotonic() + timeout if timeout else None
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

    def stop_at_deadline(reason):
        nonlocal latest_activity
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
        try:
            # Providers are launched with start_new_session=True, so their pid
            # is a private process-group leader.  Recheck that invariant before
            # signalling a group; a mock or non-conforming child only receives
            # a direct signal.
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, signal.SIGTERM)
            else:
                os.kill(child.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

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
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    if watchdog_errors:
        raise ProcessError("Activity supervision failed; tracked workers have been stopped") from watchdog_errors[0]
    return child.wait(timeout=1), watchdog_fired.is_set()
