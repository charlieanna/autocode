"""Private local-OS-user acknowledgement receipts, outside editable task trees.

Reads never create storage. Acknowledgement changes no task state, controller,
token, usage or budget. Atomic receipts reconcile a lost acknowledgement reply.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MAX_BYTES = 4096
IDENTITY = re.compile(r"[0-9a-f]{64}\Z")


def storage():
    selected = os.environ.get("AUTOCODE_ATTENTION_HOME")
    if not selected:
        selected = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "autocode" / "attention"
    return Path(selected).expanduser().absolute()


def _checked(path, *, directory=False):
    metadata = path.stat(follow_symlinks=False)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not hasattr(os, "getuid")
        or not expected(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ValueError("Attention storage must be private to its local OS owner")


def _root(workspace, *, create=False):
    root = storage()
    if (
        root.is_symlink()
        or root.is_relative_to(Path(workspace).absolute())
        or root.resolve().is_relative_to(Path(workspace).resolve())
    ):
        raise ValueError("Attention storage must be outside the editable task workspace")
    if not hasattr(os, "getuid"):
        raise ValueError("Verified local OS ownership is unavailable")
    if create:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if root.exists():
        _checked(root, directory=True)
    return root


def _identity(item_id):
    if not isinstance(item_id, str) or not IDENTITY.fullmatch(item_id):
        raise ValueError("Invalid attention identity")
    return item_id


def _read(root, item_id, workspace, run_dir):
    path = root / (_identity(item_id) + ".json")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("Attention receipts must be private regular files owned by the operator")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Attention receipt is too large")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or value.get("item_id") != item_id
        or type(value.get("actor")) is not int
        or value.get("actor") != os.getuid()
        or not isinstance(value.get("acknowledged_at"), str)
        or not value["acknowledged_at"]
        or value.get("workspace") != str(Path(workspace).resolve())
        or value.get("run_dir") != str(Path(run_dir).resolve())
    ):
        raise ValueError("Invalid attention acknowledgement receipt")
    return {key: value[key] for key in ("version", "item_id", "actor", "workspace", "run_dir", "acknowledged_at")}


def inspect(item_id, workspace, run_dir):
    try:
        root = _root(workspace)
        record = _read(root, item_id, workspace, run_dir)
        return {"available": True, "state": "acknowledged" if record else "unread", "receipt": record}
    except (OSError, ValueError, TypeError):
        return {"available": False, "state": "unknown", "reason": "Local acknowledgement storage could not be verified"}


def acknowledge(item_id, workspace, run_dir):
    item_id = _identity(item_id)
    root = _root(workspace, create=True)
    lock = root / ".ack.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+") as stream:
        _checked(lock)
        util.lock_file(stream, blocking=True)
        try:
            record = _read(root, item_id, workspace, run_dir)
            if record is None:
                record = {
                    "version": 1,
                    "item_id": item_id,
                    "actor": os.getuid(),
                    "workspace": str(Path(workspace).resolve()),
                    "run_dir": str(Path(run_dir).resolve()),
                    "acknowledged_at": util.now(),
                }
                util.atomic_json(root / (item_id + ".json"), record)
            return record
        finally:
            util.unlock_file(stream)
