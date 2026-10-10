"""Keep process ownership/cleanup independent of controller checkpoint I/O.

One worker owns process discovery and cleanup. It publishes whole receipts;
only the calling controller thread invokes persistence callbacks. Receipts may
be coalesced because each contains the complete set of known birth identities.
No callback or process operation survives a successful join.
"""

from __future__ import annotations

import threading


class ReceiptWorker:
    def __init__(self):
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.latest = None
        self.published = 0
        self.checkpoint_failed = False
        self.error = None
        self.thread = None
        self.started = False

    def record(self, rows):
        value = [dict(row) for row in rows]
        previous = self.latest
        if previous is None or previous[1] != value:
            self.latest = (1 if previous is None else previous[0] + 1, value)

    def flush(self, checkpoint):
        latest = self.latest
        if latest is not None and latest[0] != self.published:
            try:
                checkpoint([dict(row) for row in latest[1]])
            except Exception:
                self.checkpoint_failed = True
                raise
            self.published = latest[0]

    def start(self, work):
        ready = threading.Event()

        def own_processes():
            try:
                ready.wait()
                if self.started:
                    work()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()

        self.thread = threading.Thread(target=own_processes, daemon=True)
        try:
            self.thread.start()
            self.started = True
        except BaseException:
            # Thread.start can be interrupted after launching its thread. Do
            # not let it touch the tree when the caller must perform cleanup.
            self.cancel.set()
            self.started = False
            raise
        finally:
            ready.set()

    def join(self):
        if self.started:
            self.thread.join()
