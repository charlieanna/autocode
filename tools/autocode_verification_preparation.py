"""Durable native custody for scheduled clean-copy preparation.

No controller or run-state dependency. The scheduler supplies an admission;
the script entry supplies existing tree operations to the owned worker.
"""

import json
import os
import shlex
import sys
import tempfile
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

try:
    from . import autocode_command_supervision as commands
    from . import autocode_process as processes
    from . import autocode_supervision as supervision
    from . import autocode_util as util
    from . import autocode_verification_recovery as recovery
except ImportError:
    import autocode_command_supervision as commands
    import autocode_process as processes
    import autocode_supervision as supervision
    import autocode_util as util
    import autocode_verification_recovery as recovery

ADMISSION = ContextVar("verification_preparation_admission", default=None)
PHASE = ContextVar("verification_preparation_phase", default=None)
# Separate from the configured inner command timeout. Existing client/stage
# deadlines still supervise the whole operation; this is not a runtime grant.
PREPARATION_ALLOWANCE_SECONDS = 900


def allocate(out, identity, attempt, started_at):
    """Allocate immutable context before pending publication or a Git child."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    pid = os.getpid()
    owner = processes.identity(processes.process_table({pid})[pid])
    worker_path = Path(__file__).with_name("autocode_verification_prepare_worker.py").resolve()
    admission = {
        "schema": 1,
        "attempt_id": attempt,
        "identity": util.digest(identity),
        "started_at": started_at,
        "nonce": uuid.uuid4().hex,
        "out": str(out),
        "owner": owner,
        "worker": str(worker_path),
        "python": sys.executable,
        "worker_sha256": util.file_hash(worker_path),
    }
    path = out / "preparation-admission.json"
    util.atomic_json(path, admission)
    return {"path": str(path), "sha256": util.file_hash(path)}


@contextmanager
def admitted(reference):
    token = ADMISSION.set(reference)
    try:
        yield
    finally:
        ADMISSION.reset(token)


def active():
    return ADMISSION.get() is not None


def _publish_request(path, payload):
    """Bound and durably publish the exact compact envelope the worker reads."""
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(data) > recovery.MAX_PREPARATION_REQUEST_BYTES:
        raise ValueError("Preparation request exceeds the 12 MiB envelope bound")
    descriptor, temporary = tempfile.mkstemp(prefix=".preparation-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launch(operation, workspace, out, parameters, *, timeout, env):
    """Run preparation/removal only after keeper admission has been persisted."""
    reference = ADMISSION.get()
    if reference is None:
        raise ValueError("Scheduled preparation has no admission")
    out = Path(out).resolve()
    admission, admission_sha = recovery.read_regular(reference["path"], out)
    if admission_sha != reference["sha256"] or admission["out"] != str(out):
        recovery.hold("preparation admission changed before launch")
    stem = "preparation-" + uuid.uuid4().hex
    request = out / (stem + ".json")
    result = out / (stem + "-result.json")
    payload = {
        "schema": 1,
        "operation": operation,
        "workspace": str(Path(workspace).resolve()),
        "out": str(out),
        "parameters": parameters,
        "result": str(result),
        "preparation_admission": reference,
        "nonce": admission["nonce"],
    }
    # Unsupported mutable objects must fail before bootstrap, never fall back to
    # unsupervised preparation. Scheduled replay currently supplies JSON paths.
    _publish_request(request, payload)
    request_sha = util.file_hash(request)
    command = shlex.join(
        [sys.executable, admission["worker"], "--request", str(request), "--request-sha256", request_sha]
    )
    phase = {
        "phase": {"prepare": "preparing", "remove": "removing", "execute": "executing"}[operation],
        "request": str(request),
        "request_sha256": request_sha,
    }
    token = PHASE.set(phase)
    try:
        envelope = timeout + PREPARATION_ALLOWANCE_SECONDS if operation == "execute" else timeout
        receipt = commands.run(command, Path(workspace).resolve(), out / (stem + ".log"), timeout=envelope, env=env)
    finally:
        PHASE.reset(token)
    if receipt.get("exit_code") != 0 or receipt.get("timed_out") or receipt.get("error"):
        raise ValueError("Owned scratch preparation failed: " + str(receipt.get("error") or receipt.get("tail")))
    answer, _ = recovery.read_regular(result, out)
    if (
        answer.get("request_sha256") != request_sha
        or answer.get("operation") != operation
        or answer.get("nonce") != admission["nonce"]
    ):
        recovery.hold("preparation worker result does not bind its request")
    if operation == "prepare":
        tree = Path(answer["tree"])
        if tree != out / "scratch" / "tree" or tree != tree.resolve() or not tree.is_dir():
            recovery.hold("preparation worker returned an unowned tree")
        return tree
    if operation == "execute":
        if not isinstance(answer.get("receipt"), dict):
            recovery.hold("preparation worker did not return the collected inner receipt")
        inner = answer["receipt"]
        if not recovery.collected_command(inner, out):
            recovery.hold("preparation worker returned unknown or uncollected inner command ownership")
        return inner
    return None


def frontier(root, runner_check=None):
    """Read exact current obligation metadata; never reconcile or grant retry."""
    root = Path(root)
    pending = root / "pending.json"
    if not pending.exists() and not pending.is_symlink():
        return None
    try:
        active, pending_sha = recovery.read_regular(pending, root)
        digest = recovery.admission_identity(active)
        out = root / digest / active["attempt_id"]
        check = None
        reference = active.get("preparation_admission")
        if reference:
            bound = recovery.bind(root, active, None, allow_idle=True)
            out, check = bound["out"], bound["check"]
        result = {
            "attempt_id": active["attempt_id"],
            "identity": digest,
            "started_at": active.get("started_at"),
            "pending_sha256": pending_sha,
            "phase": active.get("phase") or "legacy_unbound",
            "output_directory": str(out),
            "preparation_admission": reference,
            "current_check": check,
            "runner_check_matches_current": bool(
                check
                and runner_check
                and all(check.get(k) == runner_check.get(k) for k in ("command", "output", "supervision"))
            ),
            "recovery_authorized": False,
        }
        if reference:
            result["owner"] = bound.get("owner") or check["supervision"]["owner"]
            if check:
                result["liveness"] = supervision.observe(bound["metadata"])
        else:
            result["reason"] = "Legacy obligation has no authenticated preparation custody; it remains held."
        return result
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
        SystemError,
        OverflowError,
        recovery.receipts.OwnershipUncertain,
        processes.ProcessError,
    ) as error:
        return {"phase": "unavailable", "reason": str(error), "recovery_authorized": False}
