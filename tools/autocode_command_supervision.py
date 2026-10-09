"""Owned shell-command execution for verification and replay.

The actual command remains the caller's Popen child. An independent keeper
bounds its native process tree when the caller disappears. Admission and final
records live beside retained output, never inside a disposable scratch tree.
This module does not read or write run state; runner_check binds CHECKPOINT.
"""
from __future__ import annotations

import hashlib
import subprocess
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_util as util

CHECKPOINT = ContextVar("verification_command_checkpoint", default=None)
TAIL_CHARS = 4000


def processes_absent(metadata):
    """Whether a readable terminal receipt's recorded processes are all gone.

    An armed or stopping receipt is not a finished inventory. Unreadable bytes,
    a live process, or denied inspection return false so the hold stays.
    Absence is not command evidence.
    """
    final = receipts.load(metadata)
    if not final or final.get("phase") not in ("stopped", "uncertain"):
        return False
    rows = [metadata["provider"], metadata["keeper"], metadata["owner"], *final["processes"]]
    return not processes.live_processes(rows)


def reconcile(metadata):
    """Allow retry only after authenticated cleanup and fresh native absence.

    Owner death invalidates proof but a completed keeper cleanup permits a new
    command. Denied inspection or an unfinished/malformed receipt stays held.
    """
    final = receipts.load(metadata)
    if not receipts.cleanup_complete(metadata):
        raise receipts.OwnershipUncertain("Command cleanup has no authenticated terminal receipt; "
                                          f"retain ownership evidence: {metadata}")
    try:
        rows = [metadata["provider"], metadata["keeper"], *final["processes"]]
        if processes.live_processes(rows):
            raise receipts.OwnershipUncertain("Owned verification processes are still alive; reconcile them before retrying")
    except (processes.ProcessError, OSError, ValueError, KeyError, TypeError) as error:
        raise receipts.OwnershipUncertain("Cannot verify native command cleanup; retry remains blocked") from error
    return final


def run(command, cwd, log_path, *, timeout, env, checkpoint=None):
    """Execute the existing shell command with durable before-exec ownership."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    directory = log_path.parent / "command-supervision" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    started = time.monotonic()
    deadline = started + timeout if timeout is not None else None
    process = metadata = None
    exit_code = None
    timed_out = False
    ownership_error = None

    def admit(value):
        nonlocal metadata
        metadata = value
        util.atomic_json(directory / "admission.json", {
            "schema": 1, "command": command, "cwd": str(Path(cwd).resolve()),
            "output": str(log_path.resolve()), "timeout_seconds": timeout,
            "deadline_monotonic": deadline, "supervision": value,
        })
        callback = checkpoint if checkpoint is not None else CHECKPOINT.get()
        if callback is not None:
            callback(command, log_path, value)

    try:
        with log_path.open("wb") as output:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                timed_out = True
            else:
                with supervision.launch(["/bin/sh", "-c", command],
                        receipt_path=directory / "supervision.json", timeout=remaining, checkpoint=admit,
                        cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True, env=env) as process:
                    remaining = None if deadline is None else max(0, deadline - time.monotonic())
                    try:
                        exit_code = process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        # The keeper owns deadline escalation. Reap only this actual
                        # child; never substitute its cleanup receipt for waitpid.
                        process.wait(timeout=8)
                    if deadline is not None and time.monotonic() >= deadline:
                        timed_out = True
    except BaseException as error:
        if metadata is not None:
            final = reconcile(metadata)
            if final["cause"] == "stage_deadline" and isinstance(error, supervision.SupervisionError):
                timed_out = True
            elif isinstance(error, supervision.SupervisionError):
                ownership_error = str(error)
            else:
                raise
        else:
            # Admission was never acknowledged: the bootstrap was never released.
            # launch's finally still reaps its setup children before returning.
            raise
    if metadata is not None:
        final = reconcile(metadata)
        timed_out = timed_out or final["cause"] == "stage_deadline"
    if timed_out:
        exit_code = None
    data = log_path.read_bytes()
    result = {"command": command, "exit_code": exit_code, "timed_out": timed_out,
              "duration_seconds": round(time.monotonic() - started, 2), "output": str(log_path),
              "output_sha256": hashlib.sha256(data).hexdigest(),
              "tail": data[-TAIL_CHARS:].decode("utf-8", "replace")}
    if metadata is not None:
        result.update(supervision=metadata, supervision_sha256=util.file_hash(metadata["receipt"]),
                      supervision_errors=list(process.supervision_errors) if process is not None else [])
    if ownership_error:
        result["error"] = ownership_error
        result["exit_code"] = None
    util.atomic_json(directory / "result.json", result)
    return result
