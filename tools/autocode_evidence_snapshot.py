"""Freeze this run's mutable metadata when a stage cites it as evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


def stable_path(path: Path, run_dir: Path) -> Path:
    """Freeze this run's state or activity prefix; leave other evidence unchanged.

    The evidence resolver supplies a resolved path, so aliases of these exact
    runner-owned files share the same content-addressed snapshot.
    """
    run_dir = Path(run_dir).resolve()
    if path == run_dir / "state.json":
        stem, suffix = "run-state", "json"
    elif path == run_dir / "activity.jsonl":
        stem, suffix = "run-activity", "jsonl"
    else:
        return path
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    directory = run_dir / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / f"{stem}-{digest}.{suffix}"
    if not saved.exists():
        with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{stem}-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, saved)
    if saved.read_bytes() != data:
        raise ValueError(f"Frozen {stem} evidence differs from its content hash")
    return saved
