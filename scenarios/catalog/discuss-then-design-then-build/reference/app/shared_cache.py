"""A read-through cache shared by every worker process on the host (docs/design/metadata-cache.md)."""

import fcntl
import json
import os
import tempfile
from pathlib import Path


class SharedFileCache:
    def __init__(self, directory, ttl_seconds, clock):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def _read(self, key):
        try:
            entry = json.loads((self.directory / f"{key}.json").read_text())
        except (OSError, ValueError):
            return None
        return entry if self.clock() - entry["fetched_at"] < self.ttl_seconds else None

    def get_or_fetch(self, key, fetch):
        entry = self._read(key)
        if entry is not None:
            return entry["value"]
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / f"{key}.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entry = self._read(key)
            if entry is not None:
                return entry["value"]
            value = fetch()
            handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(handle, "w") as out:
                json.dump({"fetched_at": self.clock(), "value": value}, out)
            os.replace(temporary, self.directory / f"{key}.json")
            return value
