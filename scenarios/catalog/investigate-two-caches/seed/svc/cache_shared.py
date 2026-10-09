"""A cache shared by every worker process on the host, backed by files under
var/cache/. Entries have no expiry; callers delete what they no longer want."""
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("SVC_CACHE_DIR", "var/cache"))


def get(key: str):
    try:
        return json.loads((ROOT / f"{key}.json").read_text())
    except (OSError, ValueError):
        return None


def put(key: str, value) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ROOT)
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle)
    os.replace(tmp, ROOT / f"{key}.json")


def delete(key: str) -> None:
    try:
        (ROOT / f"{key}.json").unlink()
    except FileNotFoundError:
        pass
