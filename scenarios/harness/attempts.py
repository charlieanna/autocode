"""Durable harness admissions, including attempts whose owner never returns.

These records belong to the scenario harness. They never inspect AutoCode's
private state or turn an unfinished attempt into a delivery judgement.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path

try:
    import psutil
except ModuleNotFoundError as error:
    if error.name != "psutil":
        raise
    psutil = None

MAX_RECORD_BYTES = 256 * 1024
PENDING = "PENDING_UNGRADED"
INTERRUPTED = "INTERRUPTED_UNGRADED"


def identity(pid=None):
    """Native birth identity; a reused PID is never the admitted owner."""
    if psutil is None:
        raise RuntimeError("harness attempt ownership requires psutil")
    process = psutil.Process(os.getpid() if pid is None else pid)
    born = process.create_time()
    return {
        "pid": process.pid,
        "birth_time": born,
        "group": os.getpgid(process.pid),
        "birth_identity": process._ident[1] if psutil.OSX else born,
    }


def owner_alive(owner):
    """True, False, or unknown; do not infer ownership from a PID alone."""
    if psutil is None or not isinstance(owner, dict):
        return None
    pid, birth = owner.get("pid"), owner.get("birth_identity")
    if type(pid) is not int or pid <= 0 or type(birth) not in (int, float):
        return None
    try:
        process = psutil.Process(pid)
        current = process._ident[1] if psutil.OSX else process.create_time()
        return current == birth and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return None


def read(path):
    """A bounded regular record. A malformed receipt remains unknown."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                return None
            value = json.loads(handle.read(MAX_RECORD_BYTES + 1))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def atomic_json(path, value, *, max_bytes=MAX_RECORD_BYTES):
    """Publish only after both contents and directory entry are durable."""
    path = Path(path)
    data = (json.dumps(value, indent=2) + "\n").encode()
    if max_bytes is not None and len(data) > max_bytes:
        raise ValueError("harness attempt record exceeded its bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def admit(root, result, limits):
    """Record a scenario before its first possible CLI/provider launch."""
    value = {
        **result,
        "schema": 1,
        "nonce": uuid.uuid4().hex,
        "owner": identity(),
        "phase": "pending",
        "limits": dict(limits),
        "usage_status": "unknown",
        "admitted_at": time.time(),
    }
    atomic_json(Path(root) / "attempt.json", value)
    return value


def finish(root, outcome):
    """A final result is written first; admission never manufactures one."""
    path = Path(root) / "attempt.json"
    value = read(path)
    if value is not None:
        value.update(phase="finished", verdict=outcome, finished_at=time.time())
        atomic_json(path, value)


def observe(root, view):
    """Retain reported usage so far through the public status contract only."""
    path = Path(root) / "attempt.json"
    value = read(path)
    if value is not None and isinstance(view.get("usage"), dict):
        value.update(
            usage_snapshot=totals(view["usage"]), runner_status=view.get("status"), usage_observed_at=time.time()
        )
        atomic_json(path, value)


def totals(usage):
    """The usage the record keeps: everything but the accounting's per-attempt and per-issue rows,
    which are kept as counts. A long run reports one row per model attempt, and a three-turn build
    outgrew MAX_RECORD_BYTES and stopped its harness mid-run; the run's own state keeps the rows."""
    accounting = usage.get("accounting")
    if not isinstance(accounting, dict):
        return usage
    rows = {key: row for key, row in accounting.items() if isinstance(row, list)}
    return {
        **usage,
        "accounting": {
            **{key: row for key, row in accounting.items() if key not in rows},
            **{f"{key}_rows": len(row) for key, row in rows.items()},
        },
    }


def unfinished(root):
    """Describe an admitted attempt without a readable final result."""
    root = Path(root)
    value = read(root / "attempt.json")
    if not value or value.get("schema") != 1 or not value.get("scenario") or not value.get("mode"):
        return None
    alive = owner_alive(value.get("owner"))
    # Negative exits are receipts of interruption even if the harness survived.
    calls = [
        record
        for path in sorted((root / "cli-calls").glob("*.json"))
        if (record := read(path)) and record.get("kind") == "cli_call"
    ]
    signalled = any(type(call.get("exit")) is int and call["exit"] < 0 for call in calls)
    stopped = any(call.get("phase") == "interrupted" for call in calls)
    interrupted = (
        alive is False
        or signalled
        or stopped
        or value.get("phase") == "interrupted"
        or value.get("verdict") == INTERRUPTED
    )
    reason = (
        "CLI exited on a signal"
        if signalled
        else "CLI call was interrupted"
        if stopped
        else "harness owner is no longer alive"
        if alive is False
        else "harness owner identity is unavailable"
        if alive is None
        else "harness attempt has not finished"
    )
    return {
        **value,
        "verdict": INTERRUPTED if interrupted else PENDING,
        "summary": reason,
        "evidence": str(root),
        "owner_alive": alive,
        "usage_status": "unknown",
        "metrics": {"model_seconds": None, "model_stages": None, "report_repairs": None, "tokens": None},
        "cli_calls": len(calls),
        "diagnosis": None,
        "oracle_passed": None,
        "wall_seconds": None,
        "rejected_model_calls": None,
    }
