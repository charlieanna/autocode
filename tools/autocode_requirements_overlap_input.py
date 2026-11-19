"""The one reader of the person's answers that every chat prompt shares.

Reads stop at the newline, one byte at a time, so a later prompt (or an ordinary ``input()``
consumer) can still read its own line: no prompt prefetches another prompt's input into a
private buffer, and piped answers are never lost to TextIOWrapper read-ahead. The decoder
belongs to the actual stdin stream and survives a split Unicode character. The module keeps
its name from the retired Requirements reading-time overlap (#3233), which introduced it.
"""

from __future__ import annotations

import codecs
import os
import sys

_current = None


def _unreadable(reason):
    return RuntimeError(f"Chat input cannot be read: {reason}")


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

    def line(self):
        while True:
            if "\n" in self.buffer:
                line, _, self.buffer = self.buffer.partition("\n")
                return line[:-1] if line.endswith("\r") else line
            if self.eof:
                if self.buffer:
                    line, self.buffer = self.buffer, ""
                    return line
                raise EOFError
            self.buffer += self.chunk()


class _InputProxy:
    """Keep input()'s prompting/EOF semantics without TextIOWrapper read-ahead."""

    def __init__(self, reader):
        self.reader = reader

    def fileno(self):
        # Force input() through readline even on a tty. Its C readline path would
        # bypass a partial Unicode character retained by the reader.
        return -1

    def readline(self):
        try:
            return self.reader.line() + "\n"
        except EOFError:
            return ""

    def __getattr__(self, name):
        return getattr(self.reader.stream, name)


def read(prompt):
    """Show ``prompt`` and return the person's next line, as ``input()`` does."""
    global _current
    stream = sys.stdin
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError, TypeError):
        return input(prompt)
    if _current is None or _current.stream is not stream:
        _current = _Reader(stream, fileno)
    sys.stdin = _InputProxy(_current)
    try:
        return input(prompt)
    finally:
        sys.stdin = stream
