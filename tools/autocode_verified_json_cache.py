"""Invocation-local reuse of validation for exact, freshly reread JSON bytes.

This supplies no file, path, ownership or chain authority. The caller still
rereads files and checks those facts on every use. Returned objects never alias
the retained validated value. Once the byte budget is full, new values remain
uncached rather than evicting a repeatedly read history prefix.
"""

from copy import deepcopy


class VerifiedJSONCache:
    def __init__(self, validate, *, max_bytes=64 * 1024 * 1024):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("JSON cache needs a nonnegative byte bound")
        if not callable(validate):
            raise TypeError("JSON cache needs its fixed validator")
        self._validate = validate
        self._max_bytes = max_bytes
        self._bytes = 0
        self._values = {}

    def validated(self, raw):
        if not isinstance(raw, bytes):
            raise TypeError("JSON cache needs immutable bytes")
        if raw in self._values:
            return deepcopy(self._values[raw])
        value = self._validate(raw)
        if len(raw) <= self._max_bytes - self._bytes:
            self._values[raw] = deepcopy(value)
            self._bytes += len(raw)
        return value
