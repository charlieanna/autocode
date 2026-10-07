"""Process-local admission into an enclosing CLI's independent supervisor."""
from contextlib import contextmanager
import os
import threading

_lock = threading.Lock()
_guard = None


@contextmanager
def enclosing_guard(admit):
    """All launch threads in this process share one bounded admission channel."""
    global _guard
    token = (os.getpid(), admit)
    with _lock:
        if _guard is not None:
            raise RuntimeError('An enclosing supervision guard is already installed')
        _guard = token
    try:
        yield
    finally:
        with _lock:
            if _guard is token:
                _guard = None


def admit(metadata, deadline):
    with _lock:
        guard = _guard
    if guard is not None and guard[0] == os.getpid():
        guard[1](metadata, deadline)
