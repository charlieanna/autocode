"""One asynchronous core-named acknowledgement, never an automatic retry.

The short CLI and its read children own a separate group from task workers.
Timeout/viewer cleanup reaps that group; the durable core receipt reconciles an
acknowledgement saved before a lost reply. No task authority is transferred.
"""

import contextlib
import copy
import json
import os
import signal
import subprocess
import tempfile
import time

try:
    from . import autocode_chat_polling as polling
except ImportError:
    import autocode_chat_polling as polling

MAX_REPLY = 16384


class Pending:
    def __init__(self, command, argv, row, *, env, cwd, clock=time.monotonic, launch=subprocess.Popen):
        self.clock, self.started = clock, clock()
        self.row = copy.deepcopy(row)
        self.output = tempfile.TemporaryFile()  # noqa: SIM115 - asynchronous child owns the capture until reaped
        self.closed = False
        try:
            self.child = launch(
                [*command, *argv],
                stdin=subprocess.DEVNULL,
                stdout=self.output,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=cwd,
                process_group=0,
            )
        except BaseException:
            self.output.close()
            raise

    def poll(self):
        if self.closed:
            return None
        code = self.child.poll()
        if code is None:
            if self.clock() - self.started < polling.READ_TIMEOUT:
                return None
            self.close()
            return {
                "acknowledged": False,
                "reason": "Acknowledgement outcome needs inspection; retrying the same alert reconciles its receipt.",
            }
        self.child.wait()
        self.output.seek(0)
        raw = self.output.read(MAX_REPLY + 1)
        self.close()
        try:
            value = json.loads(raw) if len(raw) <= MAX_REPLY else None
            receipt = value.get("receipt") if isinstance(value, dict) else None
            item = (self.row.get("attention") or {}).get("item") or {}
            if (
                code
                or not isinstance(value, dict)
                or value.get("acknowledged") is not True
                or not isinstance(receipt, dict)
                or any(
                    receipt.get(field) != item.get(field if field != "item_id" else "id")
                    for field in ("item_id", "workspace", "run_dir")
                )
            ):
                raise ValueError("unconfirmed acknowledgement")
            return {"acknowledged": True, "receipt": receipt}
        except (ValueError, TypeError):
            return {
                "acknowledged": False,
                "reason": "Alert was not acknowledged or its reply is unavailable; refresh and inspect the same alert.",
            }

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                if self.child.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait()
            finally:
                self.output.close()
