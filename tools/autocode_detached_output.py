"""Keep a detached terminal from interrupting a durable run.

A caller can close its stdout/stderr pipe, or the controlling terminal can hang up,
while a provider is still working. Progress output must not turn that run into an
uncertain stage, nor its saved pause into a traceback. The CLI entry wraps both
streams; this module imports nothing from AutoCode.
"""

import errno

# A closed pipe fails a write with EPIPE, a hung-up terminal with EIO (#454).
GONE = frozenset({errno.EPIPE, errno.EIO})


class _DiscardedOutput:
    def write(self, value):
        return len(value)

    def flush(self):
        pass


class DetachedOutput:
    """Write through to a stream until its reader goes away, then discard."""

    def __init__(self, stream):
        self.original = stream
        self.stream = stream

    def write(self, value):
        try:
            return self.stream.write(value)
        except OSError as error:
            if error.errno not in GONE:
                raise
            self.stream = _DiscardedOutput()
            return self.stream.write(value)

    def flush(self):
        try:
            self.stream.flush()
        except OSError as error:
            if error.errno not in GONE:
                raise
            self.stream = _DiscardedOutput()

    def __getattr__(self, name):
        return getattr(self.original, name)
