"""File-backed cache shared by all worker processes on a host."""
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("METADATA_CACHE_DIR", "/tmp/metadata-cache"))


def get(key: str, ttl_seconds: float):
    path = ROOT / f"{key}.json"
    try:
        if time.time() - path.stat().st_mtime < ttl_seconds:
            return json.loads(path.read_text())
    except OSError:
        pass
    return None


def put(key: str, value) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ROOT)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
    os.replace(tmp, ROOT / f"{key}.json")
