"""Keep a detached terminal from interrupting a durable run.

A caller can close its stdout/stderr pipe while a provider is still working.
Progress output must not turn that run into an uncertain stage. The CLI entry
wraps both streams; this module imports nothing from AutoCode.
"""
import errno


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
            if error.errno != errno.EPIPE:
                raise
            self.stream = _DiscardedOutput()
            return self.stream.write(value)

    def flush(self):
        try:
            self.stream.flush()
        except OSError as error:
            if error.errno != errno.EPIPE:
                raise
            self.stream = _DiscardedOutput()

    def __getattr__(self, name):
        return getattr(self.original, name)
