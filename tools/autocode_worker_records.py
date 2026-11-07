"""Launch-intent receipts for one core invocation, separate from task run state.

Only an initially prepared receipt can cross the dispatch boundary, once. A
repeated request returns the retained record; it is never replay permission.
Callers supply the configured storage root and their public run identities.
"""

from __future__ import annotations

import copy
import os
import re
import stat
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

IDENTITY = re.compile(r"[0-9a-f]{32}\Z")
PHASES = frozenset({"prepared", "dispatching", "bound"})


def request(*, workspace, run_dir, request_id, argv, source, boot_id, expected_checkpoint=None, authority_hash=None):
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256:
        raise ValueError("A durable request requires a stable request ID")
    if any(ord(char) < 32 for char in request_id):
        raise ValueError("Invalid durable request ID")
    if not argv or not isinstance(argv, list) or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
        raise ValueError("A durable request requires literal arguments")
    if not isinstance(source, dict) or not source or not isinstance(boot_id, str) or not boot_id:
        raise ValueError("A durable request requires runtime and boot identity")
    if expected_checkpoint is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_checkpoint):
        raise ValueError("Invalid expected checkpoint token")
    if authority_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", authority_hash):
        raise ValueError("Invalid durable authority fingerprint")
    selected, destination = Path(workspace), Path(run_dir)
    if not selected.is_absolute() or not destination.is_absolute():
        raise ValueError("A durable request requires absolute run identities")
    return {
        "version": 1,
        "workspace": str(selected),
        "run_dir": str(destination),
        "request_id": request_id,
        "argv": list(argv),
        "source": copy.deepcopy(source),
        "boot_id": boot_id,
        "expected_checkpoint": expected_checkpoint,
        "authority_hash": authority_hash,
    }


def identity(value):
    return util.digest({key: value[key] for key in ("workspace", "run_dir", "request_id")})[:32]


def _path(root, launch_id):
    if not isinstance(launch_id, str) or not IDENTITY.fullmatch(launch_id):
        raise ValueError("Invalid durable launch ID")
    return Path(root) / (launch_id + ".json")


def _root(root, *, create=True):
    root = Path(root)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = root.stat()
    if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Durable launch storage must be an owned directory")
    if hasattr(os, "getuid") and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
        raise ValueError("Durable launch storage must be private to its owner")
    return root


def read(root, launch_id):
    _root(root, create=False)
    path = _path(root, launch_id)
    if path.is_symlink():
        raise ValueError("A durable launch receipt cannot be a symlink")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or (
        hasattr(os, "getuid") and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077)
    ):
        raise ValueError("Durable receipts must be private regular files owned by the operator")
    record = util.read_object(path)
    wanted = record.get("request")
    if not isinstance(wanted, dict) or record.get("version") != 1:
        raise ValueError("Invalid durable launch receipt")
    try:
        checked = request(
            **{
                key: wanted[key]
                for key in (
                    "workspace",
                    "run_dir",
                    "request_id",
                    "argv",
                    "source",
                    "boot_id",
                    "expected_checkpoint",
                    "authority_hash",
                )
            }
        )
    except (KeyError, TypeError) as error:
        raise ValueError("Invalid durable launch request") from error
    if checked != wanted or identity(wanted) != launch_id or record.get("id") != launch_id:
        raise ValueError("Durable launch identity differs from its request")
    if record.get("request_hash") != util.digest(wanted) or record.get("phase") not in PHASES:
        raise ValueError("Durable launch request or phase is invalid")
    count = record.get("dispatch_count")
    if type(count) is not int or count != (0 if record["phase"] == "prepared" else 1):
        raise ValueError("Durable launch dispatch boundary is invalid")
    binding = record.get("binding")
    if record["phase"] == "bound":
        if not isinstance(binding, dict) or not all(
            isinstance(binding.get(key), str) and binding[key] for key in ("workspace", "run_dir", "controller_id")
        ):
            raise ValueError("Durable launch binding is invalid")
    elif binding is not None:
        raise ValueError("An unbound launch cannot carry a controller binding")
    return record


def prepare(root, wanted):
    """Persist one intent, or return its identical prior receipt without dispatch."""
    wanted = _checked(wanted)
    root = _root(root)
    launch_id = identity(wanted)
    path = _path(root, launch_id)
    with util.run_lock(root):
        if path.exists() or path.is_symlink():
            prior = read(root, launch_id)
            if prior["request"] != wanted:
                raise ValueError("Durable request ID already names different content")
            return prior, False
        record = {
            "version": 1,
            "id": launch_id,
            "request": copy.deepcopy(wanted),
            "request_hash": util.digest(wanted),
            "phase": "prepared",
            "dispatch_count": 0,
            "created_at": util.now(),
            "binding": None,
        }
        util.atomic_json(path, record)
        return record, True


def _checked(wanted):
    if not isinstance(wanted, dict) or wanted.get("version") != 1:
        raise ValueError("Invalid durable request")
    checked = request(**{key: value for key, value in wanted.items() if key != "version"})
    if checked != wanted:
        raise ValueError("Invalid durable request")
    return checked


def begin_dispatch(root, launch_id, request_hash):
    """Seal possible dispatch before transport; ambiguity can never re-arm it."""
    root = _root(root)
    with util.run_lock(root):
        record = read(root, launch_id)
        if record["request_hash"] != request_hash:
            raise ValueError("Durable request changed before dispatch")
        if record["phase"] != "prepared":
            return record, False
        record.update(phase="dispatching", dispatch_count=1, dispatched_at=util.now())
        util.atomic_json(_path(root, launch_id), record)
        return record, True


def bind(root, launch_id, request_hash, binding):
    """Bind the one dispatched request to its actual core-owned run/owner."""
    root = _root(root)
    if not isinstance(binding, dict) or not all(
        isinstance(binding.get(key), str) and binding[key] for key in ("workspace", "run_dir", "controller_id")
    ):
        raise ValueError("Durable binding requires actual run and controller identity")
    with util.run_lock(root):
        record = read(root, launch_id)
        if record["request_hash"] != request_hash or record["phase"] == "prepared":
            raise ValueError("Durable request has no matching dispatch boundary")
        if record["phase"] == "bound":
            if record["binding"] != binding:
                raise ValueError("Durable launch is already bound to another owner")
            return record
        record.update(phase="bound", binding=copy.deepcopy(binding), bound_at=util.now())
        util.atomic_json(_path(root, launch_id), record)
        return record
