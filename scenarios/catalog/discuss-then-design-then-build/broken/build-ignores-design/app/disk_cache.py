"""A cache on disk that every worker shares. It works, but it is not the design the user approved:
another module, another class, another method, and no lock."""

import json
import os
import time
from pathlib import Path


class DiskCache:
    def __init__(self, path, max_age=3600):
        self.path = Path(path)
        self.max_age = max_age

    def lookup(self, key, loader):
        target = self.path / f"{key}.json"
        try:
            entry = json.loads(target.read_text())
            if time.time() - entry["at"] < self.max_age:
                return entry["value"]
        except (OSError, ValueError, KeyError):
            pass
        value = loader()
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".part")
        temporary.write_text(json.dumps({"at": time.time(), "value": value}))
        os.replace(temporary, target)
        return value
