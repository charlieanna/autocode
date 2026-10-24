"""Admission and binding of a service launch to the ordinary locked controller.

The service identity is checked before run setup and the checkpoint again under
the run lock. Neither a process group nor an ambient environment ID is proof.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
    from . import autocode_worker_records as records
    from . import autocode_worker_service as service
    from .autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
except ImportError:
    import autocode_util as util
    import autocode_worker_records as records
    import autocode_worker_service as service
    from autocode_control_tokens import CONTROL_TOKEN_ENV_VARS


def storage():
    configured = os.environ.get("AUTOCODE_WORKER_HOME")
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return Path(configured).expanduser().resolve() if configured else base.resolve() / "autocode" / "workers"


def boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not value:
        raise ValueError("Host boot identity is unavailable")
    return value


def runtime_identity():
    """Pin installed runtime bytes, including prompts and schemas, not just HEAD."""
    root = Path(__file__).resolve().parent
    hashed = hashlib.sha256()
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in ("__pycache__", "node_modules", ".git"))
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in (".py", ".json", ".md", ".toml", ".yaml", ".txt"):
                hashed.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    return {"path": str(root), "sha256": hashed.hexdigest()}


def outside(workspace):
    root = Path(workspace).resolve()
    if Path(__file__).resolve().is_relative_to(root) or storage().is_relative_to(root):
        raise ValueError("Durable controller code and launch storage must be outside the editable task workspace")


def observation(execution):
    """Service presence and host epoch are observations, never retry authority."""
    try:
        observed = service.UserServices().inspect(execution["launch_id"])
    except (ValueError, KeyError):
        observed = {"checked": False, "present": None}
    try:
        observed["boot_matches"] = execution["boot_id"] == boot_id()
    except (OSError, KeyError, ValueError):
        observed["boot_matches"] = None
    return observed


def load_entry(path, argv, *, services=None):
    """Only the actual manager-owned service can enter a dispatched receipt."""
    path = Path(path).absolute()
    if path.parent != storage() or path.suffix != ".json":
        raise ValueError("Durable receipt is outside the configured launch storage")
    record = records.read(path.parent, path.stem)
    wanted = record["request"]
    observed = (services or service.UserServices()).inspect(record["id"])
    if (
        record["phase"] != "dispatching"
        or wanted["argv"] != list(argv)
        or wanted["source"] != runtime_identity()
        or wanted["boot_id"] != boot_id()
        or wanted["authority_hash"]
        != util.digest({key: os.environ[key] for key in CONTROL_TOKEN_ENV_VARS if key in os.environ})
        or not observed.get("checked")
        or not observed.get("present")
        or observed.get("pid") != os.getpid()
        or not observed.get("invocation_id")
        or observed["invocation_id"] != os.environ.get("INVOCATION_ID")
    ):
        raise ValueError("Durable service, runtime, boot or literal invocation identity could not be verified")
    outside(wanted["workspace"])
    return record


def guard(args, state: RunState, run_dir, workspace):
    """Called under the existing run lock, before reconcile/configure can write."""
    record = getattr(args, "_worker_context", None)
    saved = state.get("execution") or {}
    if record is None:
        if saved.get("mode") == "durable_local":
            raise ValueError("This run uses durable execution; take its current named worker action")
        return
    wanted = record["request"]
    outside(workspace)
    if args.run_dir:
        if str(workspace) != wanted["workspace"] or str(run_dir) != wanted["run_dir"]:
            raise ValueError("Durable continuation names another run")
        if util.digest(state) != wanted["expected_checkpoint"]:
            raise ValueError("Durable action checkpoint is stale; refresh without replaying the action")
        if saved.get("mode") != "durable_local" or saved.get("runtime") != wanted["source"]:
            raise ValueError("Legacy or changed-runtime runs cannot silently acquire durable execution")
    elif wanted["expected_checkpoint"] is not None or Path(run_dir).name != Path(wanted["run_dir"]).name:
        raise ValueError("Durable new-run destination differs from its reserved destination")


def bind(args, state: RunState, run_dir, workspace, owner, write):
    """The single writer records its effective bootstrap paths and owner epoch."""
    record = getattr(args, "_worker_context", None)
    if record is None:
        return
    epoch = owner.receipt["id"]
    binding = {"workspace": str(workspace), "run_dir": str(run_dir), "controller_id": epoch}
    records.bind(storage(), record["id"], record["request_hash"], binding)
    state["execution"] = {
        "version": 1,
        "mode": "durable_local",
        "adapter": "systemd_user",
        "launch_id": record["id"],
        "controller_epoch": epoch,
        "runtime": record["request"]["source"],
        "boot_id": record["request"]["boot_id"],
    }
    write(Path(run_dir) / "state.json", state)
