"""Bounded, nonblocking public status reads for the terminal client.

Two children at most, no submitted-job queue. Round-robin admission across due
identities keeps a failed read from starving another chat. A result belongs to
one request and workspace/run pair; removing or refreshing it cancels that read.
Only the caller applies results. No controller or model actions live here.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import BinaryIO

READ_TIMEOUT = 5.0
MAX_READS = 2


@dataclass
class Read:
    child: subprocess.Popen
    output: BinaryIO
    started: float


class StatusPoller:
    def __init__(self, command, env=None, *, clock=time.monotonic, launch=subprocess.Popen):
        self.command, self.env = tuple(command), env
        self.clock, self.launch = clock, launch
        self.active: dict[tuple[str, str], Read] = {}
        self.due: dict[tuple[str, str], float] = {}
        self.last: tuple[str, str] | None = None
        self.closed = False

    @staticmethod
    def stop(read: Read) -> None:
        # Status commands own separate groups, never the task controller's group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(read.child.pid, signal.SIGKILL)
        read.child.wait()
        read.output.close()

    def refresh(self, key) -> None:
        if read := self.active.pop(key, None):
            self.stop(read)
        self.due[key] = 0

    def tick(self, targets: dict[tuple[str, str], float]) -> list[tuple[tuple[str, str], str, str]]:
        """Return (requested identity, JSON text, error); never wait for a running read."""
        if self.closed:
            return []
        now, results = self.clock(), []
        for key in list(self.due.keys() | self.active.keys()):
            if key not in targets:
                if read := self.active.pop(key, None):
                    self.stop(read)
                self.due.pop(key, None)
        for key, read in list(self.active.items()):
            code = read.child.poll()
            if code is None and now - read.started < READ_TIMEOUT:
                continue
            self.active.pop(key)
            self.due[key] = now + targets[key]
            if code is None:
                self.stop(read)
                results.append((key, "", f"status read timed out after {READ_TIMEOUT:g}s"))
                continue
            read.child.wait()
            read.output.seek(0)
            raw = read.output.read()
            read.output.close()
            error = f"status read exited {code}" if code else ""
            results.append((key, raw.decode("utf-8", errors="replace") if not error else "", error))
        keys = list(targets)
        if self.last in keys:
            start = keys.index(self.last) + 1
            keys = keys[start:] + keys[:start]
        for key in keys:
            if len(self.active) >= MAX_READS:
                break
            if key in self.active or now < self.due.get(key, 0):
                continue
            self.last = key
            output = tempfile.TemporaryFile()  # noqa: SIM115 - owned until this asynchronous read is reaped
            try:
                child = self.launch(
                    [*self.command, "--status", "--workspace", key[0], "--run-dir", key[1]],
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    cwd=key[0],
                    env=self.env,
                    process_group=0,
                )
            except OSError as error:
                output.close()
                self.due[key] = now + targets[key]
                results.append((key, "", f"status read unavailable: {error}"))
                continue
            self.active[key] = Read(child, output, now)
        return results

    def close(self) -> None:
        self.closed = True
        for read in list(self.active.values()):
            self.stop(read)
        self.active.clear()
        self.due.clear()
