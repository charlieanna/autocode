"""The birth-bound owner of one CLI invocation and its saved exit boundary.

Only the holder of the existing run lock starts/finishes an owner. Status
inspects without writes; an advancing caller persists a projected lost-owner
pause under that lock. No process is signalled and unknown inspection is never
treated as absence. Deliberate user-action/unit exits retain a ready receipt.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import stat
import uuid
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_process as processes
    from . import autocode_run_status as run_status
    from . import autocode_supervision as supervision
    from . import autocode_util as util
except ImportError:
    import autocode_checkout_lock as checkout_lock
    import autocode_process as processes
    import autocode_run_status as run_status
    import autocode_supervision as supervision
    import autocode_util as util

FILE = "controller-owner.json"
STATUS = "PAUSED_CONTROLLER_EXIT"
MAX_BYTES = 16384
# A launch marker also retains argv/context; its process identities use the same
# strict reader, but it is not a small controller receipt. Keep both bounds finite.
MAX_PROCESS_MARKER_BYTES = 1024 * 1024
_owners: dict[str, Owner] = {}
_MISSING = object()


def now():
    return dt.datetime.now(dt.UTC).isoformat()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Controller receipt has duplicate fields")
        result[key] = value
    return result


READ_ATTEMPTS = 4


def _read_json(path, *, max_bytes=MAX_BYTES):
    """One consistent read of the receipt, or ValueError.

    The controller replaces the receipt atomically, so a viewer's read can straddle a checkpoint:
    the file it opened is no longer the one at the path. That is a moment, not a lost owner;
    read again a bounded number of times before reporting the ownership as uninspectable (#2304).
    """
    for attempt in range(READ_ATTEMPTS):
        try:
            before = path.lstat()
        except FileNotFoundError:
            return _MISSING
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes:
            raise ValueError("Controller receipt is not a bounded owned regular file")
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                opened = os.fstat(stream.fileno())
                data = stream.read(max_bytes + 1)
                finished = os.fstat(stream.fileno())
            after = path.lstat()
        except FileNotFoundError:
            if attempt + 1 == READ_ATTEMPTS:
                raise
            continue  # replaced between the stat and the open: read the new one
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if all(getattr(before, key) == getattr(value, key) for value in (opened, finished, after) for key in fields):
            break
        if attempt + 1 == READ_ATTEMPTS:
            raise ValueError("Controller receipt changed while inspected")
    if len(data) != before.st_size or len(data) > max_bytes:
        raise ValueError("Controller receipt was not read completely")
    value = json.loads(
        data,
        object_pairs_hook=_object,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite controller receipt")),
    )
    return value


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _timestamp(value):
    return isinstance(value, str) and dt.datetime.fromisoformat(value).utcoffset() is not None


def _read(run_dir):
    value = _read_json(Path(run_dir) / FILE)
    if value is _MISSING:
        return None
    identity = value.get("identity") if isinstance(value, dict) else None
    required = {
        "schema",
        "id",
        "identity",
        "started_at",
        "last_checkpoint_at",
        "last_stage",
        "checkpoint",
        "disposition",
        "exit_code",
        "exited_at",
        "reason",
    }
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or not isinstance(identity, dict)
        or type(identity.get("pid")) is not int
        or identity["pid"] <= 0
        or type(identity.get("birth_identity")) not in (int, float)
        or identity["birth_identity"] <= 0
        or not math.isfinite(identity["birth_identity"])
        or value.get("disposition") not in ("running", "ready", "boundary", "unexpected")
        or not _hex(value.get("id"), 32)
        or not _hex(value.get("checkpoint"), 64)
        or not _timestamp(value.get("started_at"))
        or not _timestamp(value.get("last_checkpoint_at"))
        or (value.get("last_stage") is not None and not isinstance(value["last_stage"], str))
        or (value.get("reason") is not None and not isinstance(value["reason"], str))
        or (value.get("exit_code") is not None and type(value["exit_code"]) is not int)
        or (value["disposition"] == "running" and (value["exit_code"] is not None or value["exited_at"] is not None))
        or (value["disposition"] != "running" and not _timestamp(value.get("exited_at")))
        or (value["disposition"] == "ready" and value["exit_code"] not in (0, 2))
    ):
        raise ValueError("Controller receipt has invalid ownership metadata")
    return value


@contextlib.contextmanager
def _free_lock(path):
    """Probe an existing kernel lock without creating or rewriting its file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        yield True
        return
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Controller exclusion lock is not a regular file")
        try:
            util.lock_file(stream.fileno())
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            util.unlock_file(stream.fileno())


def _worker_ownership(state: RunState, run_dir):
    """Distinguish positively live workers from absent or uninspectable ownership."""
    records = []
    for record in (state.get("active_stage"), state.get("active_runner_check")):
        if record:
            records.append(record)
    for record in (state.get("orchestration_batch") or {}).get("workers", []):
        if record.get("pid") or record.get("processes") or record.get("supervision"):
            records.append(record)
    uncertain = False
    for record in records:
        observed = processes.recorded_worker_state(record)
        if observed.get("checked") is True and observed.get("alive") is True:
            return "busy", "Birth-bound provider workers are still alive"
        if observed.get("checked") is not True or observed.get("alive") is not False:
            uncertain = True
    marker = _read_json(Path(run_dir) / "active-processes.json", max_bytes=MAX_PROCESS_MARKER_BYTES)
    if marker is not _MISSING:
        if not isinstance(marker, dict) or not isinstance(marker.get("processes"), list) or not marker["processes"]:
            uncertain = True
        else:
            observed = processes.recorded_worker_state({"processes": marker["processes"]})
            if observed.get("checked") is True and observed.get("alive") is True:
                return "busy", "Birth-bound provider workers are still alive"
            if observed.get("checked") is not True or observed.get("alive") is not False:
                uncertain = True
    return ("unknown", "Provider ownership cannot be verified absent") if uncertain else ("absent", None)


def _completed_stage(state: RunState, run_dir):
    """A collected, accounted report may be rechecked; an uncertain attempt may not."""
    record = state.get("active_stage")
    if (
        not isinstance(record, dict)
        or record.get("accounted") is not True
        or type(record.get("exit_code")) is not int
        or record["exit_code"] != 0
        or record.get("timed_out")
        or not record.get("finished_at")
        or not _timestamp(record["finished_at"])
        or not isinstance(record.get("output"), str)
        or not isinstance(record.get("supervision"), dict)
    ):
        return False
    output = Path(record["output"])
    if output.is_symlink() or not output.is_file() or not output.resolve().is_relative_to(Path(run_dir).resolve()):
        return False
    observed = supervision.observe(record["supervision"])
    if not isinstance(observed, dict) or not isinstance(observed.get("receipt"), dict):
        return False
    receipt = observed["receipt"]
    return (
        receipt.get("phase") == "stopped"
        and receipt.get("cause") in ("provider_stopped", "controller_finished")
        and receipt.get("cleanup_error") is None
        and all(
            isinstance(observed.get(key), dict)
            and observed[key].get("checked") is True
            and observed[key].get("alive") is False
            for key in ("owner", "keeper", "provider")
        )
    )


def inspect(state: RunState, run_dir, workspace, *, run_locked=False, checkout_locked=False):
    """Fresh read-only controller inspection; no saved status substitutes for it."""
    result = {
        "kind": "unknown",
        "reason": "Controller inspection unavailable",
        "receipt": None,
        "persisted_status": state.get("status"),
        "checked": False,
        "alive": None,
        "completed_stage": False,
    }
    try:
        receipt = _read(run_dir)
        result["receipt"] = deepcopy(receipt)
        if not receipt and state.get("status") != "RUNNING":
            result.update(kind="boundary", reason="Saved task boundary has no legacy controller receipt")
            return result
        if receipt:
            table = processes.process_table({receipt["identity"]["pid"]})
            row = table.get(receipt["identity"]["pid"])
            alive = bool(
                row and processes.matches(receipt["identity"], row) and not row.get("state", "").startswith("Z")
            )
            result.update(checked=True, alive=alive)
            if alive and receipt["disposition"] == "running":
                result.update(kind="running", reason="Birth-bound controller is alive")
                return result
            if (
                receipt["disposition"] == "ready"
                and receipt["exit_code"] in (0, 2)
                and receipt["checkpoint"] == util.digest(state)
            ):
                result.update(kind="ready", reason="Saved user action or unit handoff is ready to advance")
                return result
            if state.get("status") != "RUNNING":
                result.update(kind="boundary", reason="Controller reached the saved task boundary")
                return result
            if alive:
                result["reason"] = "Saved owner has not finished returning"
                return result
        run_probe = contextlib.nullcontext(True) if run_locked else _free_lock(Path(run_dir) / "writer.lock")
        with run_probe as free:
            if not free:
                result.update(kind="busy", reason="Another controller holds the run lock")
                return result
            checkout_probe = (
                contextlib.nullcontext(True)
                if run_locked and checkout_locked
                else _free_lock(checkout_lock.lock_path(workspace))
            )
            with checkout_probe as free:
                if not free:
                    result.update(kind="busy", reason="Another controller or provider holds the checkout lock")
                    return result
                ownership, reason = _worker_ownership(state, run_dir)
                if ownership != "absent":
                    result.update(kind=ownership, reason=reason)
                    return result
                # A disappeared birth or a legacy checkpoint with both free
                # exclusion locks can be recovered without guessing an exit code.
                result.update(
                    kind="ended",
                    reason="No controller or provider owns the saved running checkpoint",
                    completed_stage=_completed_stage(state, run_dir),
                )
                return result
    except (OSError, ValueError, TypeError, KeyError, OverflowError, processes.ProcessError) as error:
        result["reason"] = f"Controller ownership could not be inspected: {error}"
        return result


def project(state: RunState, inspection):
    """A status projection only; callers explicitly persist at writer boundaries."""
    if (
        state.get("status") != "RUNNING"
        or (state.get("active_stage") and not (inspection or {}).get("completed_stage"))
        or state.get("active_runner_check")
        or not inspection
        or inspection["kind"] in ("running", "busy", "ready", "boundary")
    ):
        return state
    shown = deepcopy(state)
    receipt = inspection.get("receipt") or {}
    current_receipt = receipt.get("checkpoint") == util.digest(state)
    code = receipt.get("exit_code") if current_receipt else None
    detail = f"exit code {code}" if code is not None else "exit code unavailable"
    checkpoint = receipt.get("last_stage") if current_receipt else None
    reason = (
        (
            f"Controller ended ({detail}) after {checkpoint or 'the last saved checkpoint'}; "
            "saved work is retained. Use --resume-paused to continue."
        )
        if inspection["kind"] == "ended"
        else inspection["reason"]
    )
    run_status.pause(shown, STATUS if inspection["kind"] == "ended" else "PAUSED_PROCESS_CHECK", reason)
    return shown


def reconcile(state: RunState, run_dir, workspace, persist, *, checkout_locked=False):
    """Called only by an advancing CLI holding its existing run lock."""
    observed = inspect(state, run_dir, workspace, run_locked=True, checkout_locked=checkout_locked)
    shown = project(state, observed)
    if shown is not state:
        cast(dict, state).clear()
        state.update(shown)
        state["paused_at"] = now()
        persist(Path(run_dir) / "state.json", state)
    return observed


def checkpoint(path, state: RunState):
    """Refresh only the current locked invocation after a durable state save."""
    if Path(path).name != "state.json":
        return
    owner = _owners.get(str(Path(path).parent.resolve()))
    if owner is not None:
        owner.checkpoint(state)


class Owner:
    def __init__(self, state: RunState, run_dir, persist):
        self.state, self.run_dir, self.persist = state, Path(run_dir), persist
        pid = os.getpid()
        row = processes.process_table({pid}).get(pid)
        if row is None:
            raise processes.ProcessError("Cannot capture controller birth identity")
        self.receipt = {
            "schema": 1,
            "id": uuid.uuid4().hex,
            "identity": processes.identity(row),
            "started_at": now(),
            "last_checkpoint_at": now(),
            "last_stage": None,
            "checkpoint": util.digest(state),
            "disposition": "running",
            "exit_code": None,
            "exited_at": None,
            "reason": None,
        }
        self.code, self.ready = None, False
        self._persistence_held = False

    def _save(self):
        util.atomic_json(self.run_dir / "controller-owners" / (self.receipt["id"] + ".json"), self.receipt)
        util.atomic_json(self.run_dir / FILE, self.receipt)

    def checkpoint(self, state: RunState):
        rows = state.get("stages") or []
        self.receipt.update(
            checkpoint=util.digest(state), last_checkpoint_at=now(), last_stage=rows[-1].get("stage") if rows else None
        )
        self._save()

    def hold_persistence(self):
        """Account for this exit without rewriting an uncertain checkpoint."""
        self._persistence_held = True
        self.ready = False

    def result(self, code, *, ready=False):
        self.code, self.ready = code, bool(ready and code in (0, 2) and not self._persistence_held)
        return code

    def __enter__(self):
        key = str(self.run_dir.resolve())
        if key in _owners:
            raise processes.ProcessError("A controller already owns this run in this process")
        # Configuration/validation may hold an unsaved in-memory preview. The
        # owner records the durable checkpoint, which status will inspect.
        self.checkpoint(util.read(self.run_dir / "state.json"))
        _owners[key] = self
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if isinstance(error, util.JSONCommitUncertain):
                self.hold_persistence()
            ready = self.ready and error is None and not self._persistence_held
            if kind is SystemExit and type(error.code) is int:
                self.code = error.code
            elif error is not None:
                self.code = None
            self.receipt.update(
                exit_code=self.code,
                exited_at=now(),
                disposition="ready" if ready else "boundary" if self.state.get("status") != "RUNNING" else "unexpected",
                reason=(
                    str(self.state.get("stop_reason") or "Saved task boundary")[:500]
                    if error is None
                    else type(error).__name__
                ),
            )
            if (
                self.state.get("status") == "RUNNING"
                and not self.state.get("active_stage")
                and not self.state.get("active_runner_check")
                and not ready
                and not self._persistence_held
            ):
                code = f"exit code {self.code}" if self.code is not None else "exit code unavailable"
                run_status.pause(
                    self.state,
                    STATUS,
                    f"Controller ended ({code}); saved work is retained. Use --resume-paused to continue.",
                    paused_at=now(),
                )
                self.persist(self.run_dir / "state.json", self.state)
            durable = util.read(self.run_dir / "state.json")
            self.receipt.update(
                disposition=(
                    "ready"
                    if ready and durable.get("status") == "RUNNING"
                    else "unexpected"
                    if durable.get("status") in ("RUNNING", STATUS)
                    else "boundary"
                ),
                reason=(
                    str(durable.get("stop_reason") or "Saved task boundary")[:500]
                    if error is None
                    else type(error).__name__
                ),
            )
            if self._persistence_held:
                self.receipt["reason"] = "Checkpoint persistence outcome held"
            self.checkpoint(durable)
            # Receipt metadata, never prompt/argv/environment/model content.
            try:
                with (self.run_dir / "activity.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "event": "controller_exit",
                                "at": self.receipt["exited_at"],
                                "pid": self.receipt["identity"]["pid"],
                                "exit_code": self.code,
                                "disposition": self.receipt["disposition"],
                                "reason": self.receipt["reason"],
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass  # Existing activity publication is best effort; the receipt is durable.
        finally:
            _owners.pop(str(self.run_dir.resolve()), None)
        return False
