"""Keep a command's output file to a byte limit while the command writes straight into it; imports nothing from AutoCode.

A command that writes straight to a file has no bound on it: a debug print in a loop wrote about 184 MB a
second, inside a 900 s timeout, until the run directory filled and the state saves failed (#1118). The command
keeps its own descriptor, so what it wrote survives even if the controller is killed. ``Watch`` polls the file
and truncates it to ``LIMIT``: the command is never blocked or stopped, a verbose command that passes still
passes, and disk use stays near the limit plus one poll's output.

The command and the watcher share one open file description, so the command's offset is shared too: a
truncation does not move it, and the file size after the next write is the total written so far. That is how the
bytes dropped are counted without ever holding them.
"""

from __future__ import annotations

import contextlib
import os
import threading

LIMIT = 64 * 1024 * 1024  # bytes of one command's output kept
INTERVAL = 0.1  # seconds between looks at the file


def notice(total, limit):
    """The line that tells a reader the retained output stops short."""
    return f"\n[autocode: output truncated at {limit} bytes; {total - limit} of {total} bytes were dropped]\n".encode()


class Watch:
    """Bounds the file behind ``descriptor`` to ``limit`` bytes (``LIMIT`` when None)."""

    def __init__(self, descriptor, limit=None):
        self.descriptor = descriptor
        self.limit = LIMIT if limit is None else limit
        self.total = 0

    def enforce(self):
        """Look at the file once; cut it back to the limit if it is past it."""
        size = os.fstat(self.descriptor).st_size
        # A final write can land after the size sample and be truncated below.
        # The shared offset survives truncation, including that otherwise lost write.
        offset = os.lseek(self.descriptor, 0, os.SEEK_CUR)
        self.total = max(self.total, size, offset)
        if size > self.limit:
            os.ftruncate(self.descriptor, self.limit)

    def finish(self):
        """Cut the file to the limit one last time and mark the cut; return {total, limit, truncated}."""
        self.enforce()
        truncated = self.total > self.limit
        if truncated:
            os.pwrite(self.descriptor, notice(self.total, self.limit), self.limit)
        return {"total": self.total, "limit": self.limit, "truncated": truncated}

    def start(self):
        """Poll in a thread until ``stop()``; the polling never raises into the command's owner."""
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self):
        while not self._stopped.wait(INTERVAL):
            with contextlib.suppress(OSError):
                self.enforce()

    def stop(self):
        self._stopped.set()
        self._thread.join()
