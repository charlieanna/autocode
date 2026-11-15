"""Retain uncertain Linux metadata ownership across independent Core invocations.

One private user store holds admission until the command's reaper or an enrolled
ancestor proves its family collected. Only the command's own receipt authorizes
its result. Process death, elapsed time and missing sampled PIDs never release
custody. A different kernel boot can retire the old record, preserving it.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
import threading
import uuid
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

try:
    from . import autocode_capture_family as family
    from . import autocode_util as util
    from .autocode_process import ProcessError
except ImportError:
    import autocode_capture_family as family
    import autocode_util as util
    from autocode_process import ProcessError

BLOCKED = "Connection checks are unavailable because an earlier check could not be confirmed stopped safely."
BUSY = "Another connection check is still running."
MAX_RECORD = 65536
POLL_SECONDS = 0.02
_ACTIVE: ContextVar[Admission | None] = ContextVar("metadata_custody_admission", default=None)
_GROUP: ContextVar[Group | None] = ContextVar("metadata_custody_group", default=None)
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


def _root(env):
    # Command environments can contain an isolated HOME for tool containment.
    # Only Core's ambient user configuration owns this cross-invocation store.
    environment = os.environ
    state = environment.get("XDG_STATE_HOME")
    if state and Path(state).is_absolute():
        base = Path(state)
    else:
        home = Path(environment.get("HOME") or Path.home())
        if not home.is_absolute():
            raise ProcessError(BLOCKED)
        base = home / ".local/state"
    target = base / "autocode/metadata"
    return target.parent.resolve() / target.name


def _boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(uuid.UUID(value)) != value:
        raise ValueError("Invalid kernel boot identity")
    return value


def _above_stdio(descriptor):
    if descriptor > 2:
        return descriptor
    import fcntl

    try:
        return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
    finally:
        os.close(descriptor)


def _open(path, flags):
    descriptor = os.open(path, flags | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    descriptor = _above_stdio(descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ProcessError(BLOCKED)
        return os.fdopen(descriptor, "r+b" if flags & os.O_RDWR else "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _private_root(root, create):
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = root.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ProcessError(BLOCKED)
    return True


def _sync(root):
    descriptor = _above_stdio(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json(path):
    with _open(path, os.O_RDONLY) as stream:
        data = stream.read(MAX_RECORD + 1)
    if len(data) > MAX_RECORD:
        raise ValueError("Metadata custody record exceeds its bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Metadata custody record is not an object")
    return value


def _pending(root):
    try:
        row = _json(root / "pending.json")
    except FileNotFoundError:
        return None
    if (
        type(row.get("schema")) is not int
        or type(row["uid"]) is not int
        or row["uid"] != os.geteuid()
        or not isinstance(row["boot_id"], str)
        or str(uuid.UUID(row["boot_id"])) != row["boot_id"]
    ):
        raise ValueError("Invalid pending metadata identity")
    if row["schema"] == 1 and set(row) == {"schema", "nonce", "boot_id", "uid", "result_file"}:
        # Existing single-attempt records use the same member validator and
        # release rule; there is no legacy cleanup exception.
        row = {
            "schema": 2,
            "group_nonce": row["nonce"],
            "boot_id": row["boot_id"],
            "uid": row["uid"],
            "attempts": [{"nonce": row["nonce"], "result_file": row["result_file"]}],
        }
    if (
        row["schema"] != 2
        or set(row) != {"schema", "group_nonce", "boot_id", "uid", "attempts"}
        or not isinstance(row["group_nonce"], str)
        or not _NONCE.fullmatch(row["group_nonce"])
        or not isinstance(row["attempts"], list)
        or not 1 <= len(row["attempts"]) <= 256
    ):
        raise ValueError("Invalid metadata group")
    seen = set()
    for member in row["attempts"]:
        if (
            not isinstance(member, dict)
            or set(member) != {"nonce", "result_file"}
            or not isinstance(member["nonce"], str)
            or not _NONCE.fullmatch(member["nonce"])
            or member["nonce"] in seen
            or member["result_file"] != "result-" + member["nonce"] + ".json"
        ):
            raise ValueError("Invalid metadata member")
        seen.add(member["nonce"])
    return row


def _clean(root, pending):
    row = _json(root / pending["result_file"])
    if (
        type(row.get("schema")) is not int
        or row["schema"] != 1
        or row.get("nonce") != pending["nonce"]
        or row.get("clean") is not True
        or type(row.get("started")) is not bool
        or type(row.get("expired")) is not bool
        or "error" in row
    ):
        return False
    started, expired, code = row["started"], row["expired"], row.get("returncode")
    if (started and type(code) is not int) or (not started and code is not None):
        return False
    if "setup_failed" in row:
        return row["setup_failed"] is True and not started and not expired and "start_errno" not in row
    if "start_errno" in row:
        return type(row["start_errno"]) is int and row["start_errno"] > 0 and not started and not expired
    # A trusted reaper can certify no children were launched after owner
    # cancellation. That releases custody, not command-result authority.
    return True


def _settled(root, pending, boot):
    """A fresh caller may recover custody without recovering a command result."""
    try:
        if _clean(root, pending):
            return True
    except (OSError, ValueError, TypeError, KeyError, ProcessError):
        # A killed inner helper may leave no primary packet. Only the enrolled
        # ancestor's kernel family receipt can replace that custody evidence.
        pass
    return family.backup_clean(root, pending["nonce"], boot)


def _matches(pending, group_nonce, members, boot=None):
    if pending is None:
        return not members
    return (
        pending["group_nonce"] == group_nonce
        and pending["attempts"] == members
        and (boot is None or pending["boot_id"] == boot)
    )


def _reconcile(root, boot, mutate, expected_group=None, expected_members=None):
    pending = _pending(root)
    if expected_group is not None and not _matches(pending, expected_group, expected_members, boot):
        raise ProcessError(BLOCKED)
    if pending is None:
        return
    reason = (
        "previous-boot"
        if pending["boot_id"] != boot
        else "collected"
        if all(
            _settled(root, member, pending["boot_id"]) if expected_group is None else _clean(root, member)
            for member in pending["attempts"]
        )
        else None
    )
    if reason is None:
        raise ProcessError(BLOCKED)
    if mutate:
        destination = root / (reason + "-" + pending["group_nonce"] + ".json")
        if destination.exists():
            raise ProcessError(BLOCKED)
        os.replace(root / "pending.json", destination)
        _sync(root)


def _checked_reconcile(root, boot, mutate, expected_group=None, expected_members=None):
    try:
        _reconcile(root, boot, mutate, expected_group, expected_members)
    except (OSError, ValueError, TypeError, KeyError, family.FamilyError):
        raise ProcessError(BLOCKED) from None


def _deadline(timeout):
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Metadata timeout must be nonnegative or None")
    return util.monotonic() + timeout


def _acquire(root, deadline):
    handle = _open(root / "admission.lock", os.O_CREAT | os.O_RDWR)
    try:
        while True:
            try:
                util.lock_file(handle)
                return handle
            except BlockingIOError:
                remaining = None if deadline is None else deadline - util.monotonic()
                if remaining is None or remaining <= 0:
                    raise ProcessError(BUSY) from None
                util.sleep(min(POLL_SECONDS, remaining))
    except BaseException:
        handle.close()
        raise


class Group:
    """One explicit authority may cover several independently captured members."""

    def __init__(self, root, deadline, boot):
        self.root, self.deadline, self.boot = root, deadline, boot
        self.nonce = uuid.uuid4().hex
        self.mutex = threading.Lock()
        self.members = []
        self.prepared = set()
        self.failed = self.closed = False

    def remaining_timeout(self):
        return None if self.deadline is None else max(0, self.deadline - util.monotonic())

    def available(self):
        if self.failed or self.closed:
            raise ProcessError(BLOCKED)

    def prepare(self, scope):
        with self.mutex:
            self.available()
            if scope.preparation_complete and scope.nonce in self.prepared:
                return
            if scope.result is not None or scope.nonce is not None:
                raise ProcessError(BLOCKED)
            if len(self.members) + len(self.prepared) >= 256:
                raise ProcessError(BLOCKED)
            scope.nonce = uuid.uuid4().hex
            self.prepared.add(scope.nonce)
            scope.result_path = self.root / ("result-" + scope.nonce + ".json")
            scope.result = _open(scope.result_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            scope.result.flush()
            os.fsync(scope.result.fileno())
            # Enrollment must be acknowledged before durable pending custody:
            # cancellation before enrollment must leave no launched member or
            # unrecoverable global hold.
            scope.backup = family.register_backup(self.root, scope.nonce, scope.effective_deadline(), boot_id=self.boot)
            scope.preparation_complete = True

    def arm(self, scope):
        self.prepare(scope)
        with self.mutex:
            self.available()
            if scope.nonce not in self.prepared:
                raise ProcessError(BLOCKED)
            member = {"nonce": scope.nonce, "result_file": scope.result_path.name}
            self.prepared.remove(scope.nonce)
            self.members.append(member)
            util.atomic_json(
                self.root / "pending.json",
                {
                    "schema": 2,
                    "group_nonce": self.nonce,
                    "boot_id": self.boot,
                    "uid": os.geteuid(),
                    "attempts": self.members,
                },
            )

    def _abort_prepared(self, scope):
        self.prepared.discard(scope.nonce)
        scope.preparation_complete = False
        if scope.backup is not None:
            # Before pending commit and GO, no target can have been launched.
            # This rolls back an unused enrollment, not uncertain custody.
            with suppress(OSError, ValueError, TypeError, family.FamilyError):
                scope.backup.release()
            scope.backup = None
        if scope.result is not None:
            with suppress(OSError):
                scope.result.close()

    def abort_prepared(self, scope):
        with self.mutex:
            if any(member["nonce"] == scope.nonce for member in self.members):
                raise ProcessError(BLOCKED)
            self._abort_prepared(scope)

    def finish(self, scope):
        with self.mutex:
            try:
                armed = any(member["nonce"] == scope.nonce for member in self.members)
                if not armed:
                    self._abort_prepared(scope)
                pending = _pending(self.root)
                if not _matches(pending, self.nonce, self.members, self.boot):
                    raise ProcessError(BLOCKED)
                # Enrollment can be interrupted before any pending member or
                # child exists. Its empty allocation must not mask cancellation.
                if armed and not _clean(self.root, {"nonce": scope.nonce, "result_file": scope.result_path.name}):
                    raise ProcessError(BLOCKED)
                if armed and scope.backup is not None:
                    # A valid primary packet already proves collection.
                    # Unregistration only avoids an unnecessary backup.
                    with suppress(OSError, ValueError, TypeError, family.FamilyError):
                        scope.backup.release()
                if self.failed:
                    raise ProcessError(BLOCKED)
            except (OSError, ValueError, TypeError, KeyError, ProcessError):
                self.failed = True
                raise ProcessError(BLOCKED) from None


class Admission:
    def __init__(self, owner, deadline):
        self.group = owner
        self.root, self.deadline, self.boot = owner.root, deadline, owner.boot
        self.result = self.result_path = self.nonce = None
        self.backup = None
        self.preparation_complete = False

    def effective_deadline(self):
        deadline = self.deadline
        if self.group.deadline is not None:
            deadline = self.group.deadline if deadline is None else min(deadline, self.group.deadline)
        return deadline

    def remaining_timeout(self):
        deadline = self.effective_deadline()
        return None if deadline is None else max(0, deadline - util.monotonic())

    def prepare(self):
        """Allocate and enroll before helper startup, without pending custody."""
        try:
            self.group.prepare(self)
        except (OSError, ValueError, TypeError, family.FamilyError):
            self.group.failed = True
            raise ProcessError(BLOCKED) from None
        return self.result, self.nonce

    def arm(self):
        """Commit custody after helper READY and before GO can start a target."""
        try:
            self.group.arm(self)
        except (OSError, ValueError, TypeError, family.FamilyError):
            self.group.failed = True
            raise ProcessError(BLOCKED) from None
        return self.result, self.nonce

    def abort_prepared(self):
        """Roll back only preparation for which pending/GO was never committed."""
        self.group.abort_prepared(self)


class _Portable:
    result = result_path = nonce = None

    def __init__(self, timeout):
        self.timeout = timeout

    def remaining_timeout(self):
        return self.timeout

    def prepare(self):
        return None, None

    def arm(self):
        return None, None

    def abort_prepared(self):
        pass


def current():
    return _ACTIVE.get() if sys.platform == "linux" else None


@contextmanager
def group(env=None, timeout=None):
    """Share explicit metadata authority through a fresh context copy per worker."""
    if sys.platform != "linux":
        yield _Portable(timeout)
        return
    deadline = _deadline(timeout)
    try:
        root = _root(env)
    except (OSError, ValueError, TypeError):
        raise ProcessError(BLOCKED) from None
    owner = _GROUP.get()
    if owner is not None:
        if owner.root != root or current() is not None:
            raise ProcessError(BLOCKED)
        with owner.mutex:
            owner.available()
            if deadline is not None:
                owner.deadline = deadline if owner.deadline is None else min(owner.deadline, deadline)
        yield owner
        return
    try:
        _private_root(root, create=True)
        handle = _acquire(root, deadline)
    except (OSError, ValueError, TypeError):
        raise ProcessError(BLOCKED) from None
    try:
        try:
            boot = _boot_id()
        except (OSError, ValueError):
            raise ProcessError(BLOCKED) from None
        _checked_reconcile(root, boot, mutate=True)
        owner = Group(root, deadline, boot)
        token = _GROUP.set(owner)
        try:
            yield owner
        finally:
            try:
                with owner.mutex:
                    owner.closed = True
                    _checked_reconcile(
                        root, boot, mutate=True, expected_group=owner.nonce, expected_members=owner.members
                    )
            finally:
                _GROUP.reset(token)
    finally:
        try:
            util.unlock_file(handle)
        finally:
            handle.close()


@contextmanager
def admission(env=None, timeout=None):
    """Admit one metadata read; child ``env`` never changes Core's custody store."""
    if sys.platform != "linux":
        yield _Portable(timeout)
        return
    owner = _GROUP.get()
    if owner is None:
        with group(env=env, timeout=timeout):
            with admission(env=env, timeout=timeout) as scope:
                yield scope
        return
    deadline = _deadline(timeout)
    if owner.root != _root(env):
        raise ProcessError(BLOCKED)
    if owner.deadline is not None:
        deadline = owner.deadline if deadline is None else min(deadline, owner.deadline)
    active = current()
    if active is not None:
        if active.group is not owner:
            raise ProcessError(BLOCKED)
        if deadline is not None:
            active.deadline = deadline if active.deadline is None else min(active.deadline, deadline)
        yield active
        return
    with owner.mutex:
        owner.available()
    scope = Admission(owner, deadline)
    token = _ACTIVE.set(scope)
    try:
        yield scope
    finally:
        try:
            owner.finish(scope)
        finally:
            _ACTIVE.reset(token)
            if scope.result is not None:
                scope.result.close()


def status(env=None):
    """Observe custody without creating, removing or repairing any file."""
    if sys.platform != "linux":
        return None
    handle = None
    try:
        root = _root(env)
        if not _private_root(root, create=False):
            return None
        try:
            handle = _open(root / "admission.lock", os.O_RDWR)
        except FileNotFoundError:
            return BLOCKED if (root / "pending.json").exists() else None
        try:
            util.lock_file(handle)
        except BlockingIOError:
            return BUSY
        _reconcile(root, _boot_id(), mutate=False)
        return None
    except (OSError, ValueError, TypeError, KeyError, ProcessError, family.FamilyError):
        return BLOCKED
    finally:
        if handle is not None:
            handle.close()
