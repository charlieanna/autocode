"""Reuse public reads within one explicitly read-only response, never between actions."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_snapshot = ContextVar("taskrun_read_snapshot", default=None)


@contextmanager
def snapshot():
    """Give one read-only response its own observations; nested responses are independent."""
    token = _snapshot.set({})
    try:
        yield
    finally:
        _snapshot.reset(token)


def read(key, load):
    """Reuse successful identical reads in this response, with no shared mutable result."""
    cache = _snapshot.get()
    if cache is None:
        return load()
    if key not in cache:
        cache[key] = deepcopy(load())
    return deepcopy(cache[key])
