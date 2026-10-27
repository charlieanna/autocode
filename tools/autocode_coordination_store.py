"""Durable multi-repository coordination records; no task private-state reads.

The coordinator owns these records beside the user's registry. A process-wide
file lock covers each advancing operation, including its durable intent, so a
restarted viewer never dispatches a second copy of an uncertain operation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

try:
    from . import autocode_registry as registry
    from . import autocode_util as util
except ImportError:
    import autocode_registry as registry
    import autocode_util as util


class CoordinationError(ValueError):
    pass


def root(run_dir):
    identity = hashlib.sha256(str(Path(run_dir).resolve()).encode()).hexdigest()[:24]
    return registry.registry_path().parent / "coordination" / identity


def read(run_dir):
    path = root(run_dir) / "coordination.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise CoordinationError("Coordination record cannot be a symlink")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise CoordinationError("Coordination record is unreadable; no operation was replayed") from error
    if value.get("version") != 1 or value.get("anchor") != str(Path(run_dir).resolve()):
        raise CoordinationError("Coordination record belongs to another run or version")
    return value


def save(document):
    document["revision"] = document.get("revision", 0) + 1
    document["updated_at"] = util.now()
    util.atomic_json(root(document["anchor"]) / "coordination.json", document)


@contextlib.contextmanager
def locked(run_dir):
    directory = root(run_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise CoordinationError("Coordination directory cannot be a symlink")
    with (directory / "advance.lock").open("a") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError as error:
            raise CoordinationError("Coordination is already advancing in another viewer") from error
        try:
            yield read(run_dir)
        finally:
            util.unlock_file(handle)


def command(run_dir, operation="advance", *arguments):
    return ["autocode", "coordinate", operation, "--run-dir", str(Path(run_dir).resolve()), *arguments]


def cancelled(run_dir):
    return (root(run_dir) / "cancel-request.json").exists()


def offer(run_dir, *, eligible=False):
    """Cheap read-only status projection; detailed reads use the coordinator API."""
    document = read(run_dir)
    if not document and not eligible:
        return None
    return {
        "version": 1,
        "status_command": command(run_dir, "status", "--json"),
        "next_command": command(run_dir, "advance", "--json"),
        "phase": (document or {}).get("phase", "discovering"),
        "revision": (document or {}).get("revision", 0),
        "cancel_command": command(run_dir, "cancel", "--json"),
        "active": (document or {}).get("active"),
        "members": [
            {
                "target": row["target"],
                "run_dir": row["run_dir"],
                "status": (document or {}).get("member_statuses", {}).get(row["target"], "STARTING"),
                "pr_url": (row.get("publication") or {}).get("url"),
            }
            for row in (document or {}).get("members", [])
        ],
    }
