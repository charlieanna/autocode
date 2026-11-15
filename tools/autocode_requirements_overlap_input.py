"""Main-thread answer input that notices interventions during Requirements overlap.

All chat prompts share the same reader. Reads stop at the newline, so switching between
ordinary input and the interruptible wait never loses TextIOWrapper read-ahead. The decoder
belongs to the actual stdin stream and survives a split Unicode character or a paused partial
line. No input thread owns a second copy of the conversation.
"""

from __future__ import annotations

import codecs
import os
import select
import sys
import time

try:
    from . import autocode_interventions as interventions
except ImportError:
    import autocode_interventions as interventions

INTERVENED = object()
POLL_SECONDS = 0.1
_current = None


def _unreadable(reason):
    return RuntimeError(f"Chat input cannot be observed while a background Requirements call runs: {reason}")


def _ready(fileno):
    try:
        return bool(select.select([fileno], [], [], 0)[0])
    except (OSError, ValueError) as error:
        raise _unreadable(f"stdin readiness failed: {error}") from error


class _Reader:
    def __init__(self, stream, fileno):
        self.stream, self.fileno = stream, fileno
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder(getattr(stream, "encoding", None) or "utf-8")(
            errors=getattr(stream, "errors", None) or "strict"
        )
        self.eof = False

    def chunk(self):
        try:
            # Never prefetch another prompt's input into a private buffer. In particular,
            # an ordinary input() consumer after this prompt can still read its own line.
            data = os.read(self.fileno, 1)
        except (OSError, ValueError) as error:
            raise _unreadable(f"stdin read failed: {error}") from error
        self.eof = not data
        return self.decoder.decode(data, final=self.eof)

    def line(self, *, waiting=False, pending=lambda: False, ready=None, chunk=None, pause=time.sleep, interval=0.1):
        injected = chunk is not None
        chunk = self.chunk if chunk is None else chunk
        ready = (lambda: _ready(self.fileno)) if ready is None else ready
        while True:
            if waiting and pending():
                return INTERVENED
            if "\n" in self.buffer:
                line, _, self.buffer = self.buffer.partition("\n")
                return line[:-1] if line.endswith("\r") else line
            if self.eof:
                if self.buffer:
                    line, self.buffer = self.buffer, ""
                    return line
                raise EOFError
            if not waiting or ready():
                data = chunk()
                # An incremental decoder may legitimately return no text before EOF.
                if injected and data == "":
                    self.eof = True
                self.buffer += data
            else:
                pause(interval)


class _InputProxy:
    """Keep input()'s prompting/EOF semantics without TextIOWrapper read-ahead."""

    def __init__(self, reader):
        self.reader = reader

    def fileno(self):
        # Force input() through readline even on a tty. Its C readline path would
        # bypass a partial Unicode character retained by the interruptible reader.
        return -1

    def readline(self):
        try:
            return self.reader.line() + "\n"
        except EOFError:
            return ""

    def __getattr__(self, name):
        return getattr(self.reader.stream, name)


def read(
    prompt, run_dir=None, waiting=False, *, pending=None, ready=None, chunk=None, pause=None, poll_seconds=POLL_SECONDS
):
    global _current
    stream = sys.stdin
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError, TypeError) as error:
        if not waiting:
            return input(prompt)
        raise _unreadable("stdin has no file descriptor") from error
    if _current is None or _current.stream is not stream:
        _current = _Reader(stream, fileno)
    reader = _current
    if not waiting:
        sys.stdin = _InputProxy(reader)
        try:
            return input(prompt)
        finally:
            sys.stdin = stream
    pending = (
        (lambda directory: directory is not None and bool(interventions.pending(directory)))
        if pending is None
        else pending
    )
    print(prompt, end="", flush=True)
    return reader.line(
        waiting=True,
        pending=lambda: pending(run_dir),
        ready=ready,
        chunk=chunk,
        pause=time.sleep if pause is None else pause,
        interval=poll_seconds,
    )
