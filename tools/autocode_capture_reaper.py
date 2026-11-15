"""Private Linux child reaper for one captured invocation.

This process owns no other invocation. Orphans remain its children across
setsid/double-fork; only a collected root status followed by ECHILD proves a
started command is finished. /proc supplies signal targets, never that proof.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import suppress

CLEANUP_SECONDS = 4.0
TERM_SECONDS = 2.0
POLL_SECONDS = 0.02
MAX_PACKET = 16 * 1024 * 1024
WAIT_ALL = 0x40000000  # Linux __WALL: include clone children with exit_signal=0.
DRAIN_BATCH = 256


def establish():
    """Establish adoption before any command can create a descendant."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    value = ctypes.c_int()
    if libc.prctl(36, ctypes.c_ulong(1), 0, 0, 0) != 0 or libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    if value.value != 1:
        raise OSError(errno.EIO, "Cannot verify capture child reaper")
    # Ignored SIGCHLD survives exec and would discard the root's exit status.
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    children()  # Refuse an unavailable inventory before launching anything.


def children():
    with open(f"/proc/self/task/{os.getpid()}/children", encoding="ascii") as stream:
        words = stream.read(MAX_PACKET + 1)
    if len(words) > MAX_PACKET:
        raise OSError(errno.EOVERFLOW, "Capture child inventory exceeds its bound")
    result = [int(word) for word in words.split()]
    if any(pid <= 0 or pid == os.getpid() for pid in result):
        raise ValueError("Invalid capture child inventory")
    return result


def drain(root, code):
    """Collect every waitable child; return root code and kernel family absence."""
    for _ in range(DRAIN_BATCH):
        try:
            pid, status = os.waitpid(-1, os.WNOHANG | WAIT_ALL)
        except ChildProcessError:
            return code, True
        if pid == 0:
            return code, False
        if pid == root and code is None:
            code = os.waitstatus_to_exitcode(status)
    # Churn must return control to deadlines/cancellation/escalation. A full
    # batch establishes no absence, even if every observed child exited.
    return code, False


def signal_children(signum, deadline):
    # This single-threaded helper is the sole reaper. Do not reap between this
    # snapshot and signalling: an exited direct child's PID remains reserved.
    for pid in children():
        if time.monotonic() >= deadline:
            raise TimeoutError("Capture signalling exceeded its cleanup bound")
        with suppress(ProcessLookupError):
            os.kill(pid, signum)


def cleanup(root, code):
    started = time.monotonic()
    while True:
        code, absent = drain(root, code)
        if absent:
            if code is None:
                raise RuntimeError("Capture root status was not collected")
            return code
        elapsed = time.monotonic() - started
        if elapsed >= CLEANUP_SECONDS:
            raise TimeoutError("Capture children remain after cleanup")
        signal_children(signal.SIGTERM if elapsed < TERM_SECONDS else signal.SIGKILL, started + CLEANUP_SECONDS)
        time.sleep(POLL_SECONDS)


def cancelled(fd, timeout=0):
    return bool(select.select([fd], [], [], timeout)[0]) and os.read(fd, 1) == b""


def execute(packet, owner):
    stopped = []

    def stop(signum, _frame):
        stopped.append(signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop)
    establish()
    deadline = packet["deadline"]
    if stopped or cancelled(owner):
        return {"started": False, "returncode": None, "expired": False}
    if deadline is not None and time.monotonic() >= deadline:
        return {"started": False, "returncode": None, "expired": True}
    try:
        child = subprocess.Popen(packet["command"], start_new_session=True, close_fds=True)
    except OSError as error:
        # Popen collects its failed exec child. Only this prelaunch failure may
        # retain ordinary exec errno semantics; cleanup errors stay uncertain.
        if not drain(None, None)[1]:
            raise RuntimeError("Failed capture exec left children") from error
        return {"started": False, "returncode": None, "expired": False, "start_errno": error.errno}
    code, expired = None, False
    try:
        while True:
            code, absent = drain(child.pid, code)
            if code is not None:
                break
            if absent:
                raise RuntimeError("Capture root status disappeared")
            if stopped or cancelled(owner):
                break
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                expired = True
                break
            cancelled_now = cancelled(owner, min(POLL_SECONDS, remaining) if remaining is not None else POLL_SECONDS)
            if cancelled_now:
                break
    finally:
        code = cleanup(child.pid, code)
        child.returncode = code  # No Popen poll/wait may compete with drain().
    return {"started": True, "returncode": code, "expired": expired}


def main():
    request, result, owner = map(int, sys.argv[1:])
    packet = None
    response = {"schema": 1, "clean": False}
    try:
        with os.fdopen(request, "rb") as stream:
            raw = stream.read(MAX_PACKET + 1)
        if len(raw) > MAX_PACKET:
            raise ValueError("Capture request exceeds its bound")
        packet = json.loads(raw)
        response["nonce"] = packet["nonce"]
        response.update(execute(packet, owner), clean=True)
    except OSError as error:
        response.update(error=type(error).__name__, errno=error.errno)
    except BaseException as error:
        response["error"] = type(error).__name__
    finally:
        os.close(owner)
        with os.fdopen(result, "wb") as stream:
            stream.write(json.dumps(response).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    return 0 if response["clean"] else 125


if __name__ == "__main__":
    raise SystemExit(main())
