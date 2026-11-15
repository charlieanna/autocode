"""Private Linux capture ancestry and family-only completion receipts.

Control sockets grant no command/result authority. A descendant can enroll a
private backup descriptor; only its ancestor reaper's kernel ECHILD proof lets
that reaper write it. Every ancestor is enrolled before metadata is launched.
"""

from __future__ import annotations

import array
import errno
import json
import os
import re
import select
import socket
import stat
import struct
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path

ENVIRONMENT_KEY = "AUTOCODE_CAPTURE_FAMILY"
MAX_ANCESTORS = 32
MAX_ENROLMENTS = 256
MAX_PACKET = 16 * 1024  # Includes the maximum endpoint chain, not only tiny RPCs.
SERVICE_BATCH = 32
CLEANUP_ALLOWANCE = 8.0
_NONCE = re.compile(r"[0-9a-f]{32}\Z")


class FamilyError(RuntimeError):
    """An advertised ancestor could not establish its private enrolment."""


def _nonce(value):
    return isinstance(value, str) and bool(_NONCE.fullmatch(value))


def _above_stdio(descriptor):
    if descriptor >= 3:
        return descriptor
    import fcntl

    try:
        return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
    finally:
        os.close(descriptor)


def _socket(kind):
    result = socket.socket(socket.AF_UNIX, kind)
    if result.fileno() >= 3:
        return result
    descriptor = _above_stdio(result.detach())
    try:
        return socket.socket(fileno=descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def channels():
    raw = os.environ.get(ENVIRONMENT_KEY)
    if raw is None:
        return []
    try:
        if len(os.fsencode(raw)) > MAX_PACKET:
            raise ValueError("Capture ancestry packet exceeds its bound")
        value = json.loads(raw)
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_ANCESTORS:
            raise ValueError("Invalid capture ancestry")
        seen = set()
        for row in value:
            if (
                not isinstance(row, dict)
                or set(row) != {"endpoint", "nonce"}
                or not isinstance(row["endpoint"], str)
                or not Path(row["endpoint"]).is_absolute()
                or len(os.fsencode(row["endpoint"])) > 107
                or "\0" in row["endpoint"]
                or not _nonce(row["nonce"])
                or row["nonce"] in seen
            ):
                raise ValueError("Invalid capture channel")
            seen.add(row["nonce"])
            endpoint = Path(row["endpoint"])
            info, directory = endpoint.lstat(), endpoint.parent.lstat()
            if (
                not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.geteuid()
                or directory.st_mode & 0o077
            ):
                raise ValueError("Capture endpoint is not private")
        return value
    except (OSError, ValueError, TypeError, KeyError):
        raise FamilyError("Capture ancestry is unavailable") from None


class Launch:
    """Validate the inherited endpoint chain without creating per-poll records."""

    def __init__(self):
        self.ancestors = channels()
        if len(self.ancestors) >= MAX_ANCESTORS:
            raise FamilyError("Capture ancestry exceeds its bound")
        self.nonce = uuid.uuid4().hex

    def packet(self):
        return {"nonce": self.nonce, "ancestors": self.ancestors}


def _wait(descriptor, deadline, *, writable=False):
    if deadline is None or time.monotonic() >= deadline:
        raise FamilyError("Capture ancestor enrolment exceeded its bound")
    remaining = deadline - time.monotonic()
    ready = select.select([] if writable else [descriptor], [descriptor] if writable else [], [], max(0, remaining))
    if not ready[1 if writable else 0]:
        raise FamilyError("Capture ancestor enrolment exceeded its bound")


def _connect(channel, deadline):
    connection = _socket(socket.SOCK_SEQPACKET)
    connection.setblocking(False)
    try:
        while True:
            if deadline is None or time.monotonic() >= deadline:
                raise FamilyError("Capture ancestor enrolment exceeded its bound")
            try:
                connection.connect(channel["endpoint"])
                break
            except BlockingIOError:
                _wait(connection, deadline, writable=True)
            except OSError as error:
                if error.errno != errno.EISCONN:
                    raise
                break
        pid, uid, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        )
        if uid != os.geteuid() or not _within(os.getpid(), pid):
            raise FamilyError("Capture endpoint is not an ancestor")
        return connection
    except BaseException:
        connection.close()
        raise


def _rpc(channel, message, descriptors, deadline):
    with _connect(channel, deadline) as connection:
        request = uuid.uuid4().hex
        payload = json.dumps({**message, "ancestor_nonce": channel["nonce"], "request": request}).encode()
        rights = array.array("i", descriptors)
        while True:
            try:
                connection.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)], socket.MSG_DONTWAIT)
                break
            except BlockingIOError:
                _wait(connection, deadline, writable=True)
        _wait(connection, deadline)
        try:
            answer = json.loads(connection.recv(MAX_PACKET))
        except (ValueError, UnicodeError):
            raise FamilyError("Capture ancestor returned no valid acknowledgement") from None
        if answer != {"request": request, "accepted": True} or answer.get("accepted") is not True:
            raise FamilyError("Capture ancestor refused enrolment")


class Registration:
    def __init__(self, attempt_nonce, enrolled=()):
        self.nonce, self.enrolled = attempt_nonce, list(enrolled)

    def release(self):
        """Best-effort nonblocking FD release after the primary child proof only."""
        for channel in self.enrolled:
            try:
                with _socket(socket.SOCK_SEQPACKET) as connection:
                    connection.setblocking(False)
                    connection.connect(channel["endpoint"])
                    connection.send(
                        json.dumps(
                            {"operation": "release", "attempt_nonce": self.nonce, "ancestor_nonce": channel["nonce"]}
                        ).encode(),
                        socket.MSG_DONTWAIT,
                    )
            except OSError:
                pass  # The bounded ancestor closes remaining descriptors at exit.
        self.enrolled.clear()


def register_backup(root, attempt_nonce, deadline, *, boot_id):
    inherited = channels()
    if not inherited:
        return Registration(attempt_nonce)
    if not _nonce(attempt_nonce) or str(uuid.UUID(boot_id)) != boot_id:
        raise FamilyError("Invalid metadata enrolment identity")
    # All callers are bounded. None must not become an infinite protocol wait.
    deadline = time.monotonic() + CLEANUP_ALLOWANCE if deadline is None else deadline
    registered = Registration(attempt_nonce)
    try:
        for channel in inherited:
            path = Path(root) / f"family-result-{attempt_nonce}-{channel['nonce']}.json"
            descriptor = _above_stdio(
                os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            )
            try:
                os.fsync(descriptor)
                _rpc(
                    channel,
                    {"operation": "register", "attempt_nonce": attempt_nonce, "boot_id": boot_id},
                    (descriptor,),
                    deadline,
                )
                registered.enrolled.append(channel)
            finally:
                os.close(descriptor)
        return registered
    except BaseException:
        # No pending member or child exists until every registration returned.
        # Roll back only this still-unarmed enrolment, never a running command.
        registered.release()
        raise


def backup_clean(root, attempt_nonce, boot_id):
    if not _nonce(attempt_nonce):
        return False
    paths = []
    for path in Path(root).glob(f"family-result-{attempt_nonce}-*.json"):
        paths.append(path)
        if len(paths) > MAX_ANCESTORS:
            raise FamilyError("Metadata backup inventory exceeds its bound")
    for path in paths:
        ancestor = path.name.removeprefix(f"family-result-{attempt_nonce}-").removesuffix(".json")
        if not _nonce(ancestor):
            continue
        descriptor = None
        try:
            descriptor = _above_stdio(os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW))
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                continue
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(MAX_PACKET + 1)
            if len(raw) > MAX_PACKET:
                continue
            try:
                packet = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if (
                packet
                == {
                    "schema": 1,
                    "family_clean": True,
                    "attempt_nonce": attempt_nonce,
                    "ancestor_nonce": ancestor,
                    "boot_id": boot_id,
                }
                and type(packet.get("schema")) is int
                and packet.get("family_clean") is True
            ):
                return True
        except OSError:
            continue  # A different enclosing family's valid proof still suffices.
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return False


def _identity(pid):
    with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
        fields = stream.read().rpartition(")")[2].split()
    return fields[19], int(fields[1])


def _within(pid, ancestor):
    """Kernel ancestry authorizes enrolment; sampled absence never releases it."""
    seen = set()
    for _ in range(256):
        if pid == ancestor:
            return True
        if pid <= 1 or pid in seen:
            return False
        seen.add(pid)
        before = _identity(pid)
        parent = _identity(before[1])
        if _identity(pid) != before or _identity(before[1]) != parent:
            return False
        pid = before[1]
    return False


def _descendant(pid):
    return _within(pid, os.getpid())


class Server:
    """One helper's bounded nonblocking listener and exclusive backup writers."""

    def __init__(self, packet):
        self.packet = packet
        self.socket = self.directory = None
        self.connections, self.registered = [], {}
        self.cleanup_deadline = None
        if packet is not None:
            self.nonce = packet["nonce"]
            self.boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            # A short private filesystem path survives ordinary close_fds Core
            # execs and is independent of the child's network namespace.
            self.directory = Path(tempfile.mkdtemp(prefix="autocode-capture-", dir="/tmp"))
            try:
                self.endpoint = self.directory / "control"
                self.socket = _socket(socket.SOCK_SEQPACKET)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                self.socket.bind(str(self.endpoint))
                self.endpoint.chmod(0o600)
                self.socket.setblocking(False)
                self.socket.listen(MAX_ENROLMENTS)
            except BaseException:
                self.close()
                raise

    def target_environment(self, environment=None):
        if self.packet is None:
            return None
        rows = [*self.packet["ancestors"], {"endpoint": str(self.endpoint), "nonce": self.nonce}]
        return {**(os.environ if environment is None else environment), ENVIRONMENT_KEY: json.dumps(rows)}

    def service(self):
        if self.socket is None:
            return
        for _ in range(SERVICE_BATCH):
            try:
                connection, _ = self.socket.accept()
            except OSError:
                break
            try:
                if len(self.connections) >= MAX_ENROLMENTS:
                    connection.close()
                else:
                    if connection.fileno() < 3:
                        connection = socket.socket(fileno=_above_stdio(connection.detach()))
                    connection.setblocking(False)
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                    self.connections.append(connection)
            except OSError:
                connection.close()
        for connection in self.connections[:SERVICE_BATCH]:
            descriptors, message = [], None
            try:
                payload, ancillary, flags, _ = connection.recvmsg(
                    MAX_PACKET,
                    socket.CMSG_SPACE(array.array("i").itemsize) + socket.CMSG_SPACE(struct.calcsize("3i")),
                    socket.MSG_DONTWAIT | socket.MSG_CMSG_CLOEXEC,
                )
            except BlockingIOError:
                self.connections.remove(connection)
                self.connections.append(connection)
                continue
            except OSError:
                connection.close()
                self.connections.remove(connection)
                continue
            try:
                credentials = None
                for level, kind, data in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        rights = array.array("i")
                        rights.frombytes(data[: len(data) - len(data) % rights.itemsize])
                        descriptors.extend(rights)
                    elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                        credentials = struct.unpack("3i", data)
                if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                    raise FamilyError("Truncated capture enrolment")
                message = json.loads(payload)
                if not isinstance(message, dict) or credentials is None:
                    raise FamilyError("Capture enrolment has no kernel credentials")
                pid, uid, _ = credentials
                if (
                    uid != os.geteuid()
                    or message.get("ancestor_nonce") != self.nonce
                    or not _nonce(message.get("attempt_nonce"))
                    or not _descendant(pid)
                ):
                    raise FamilyError("Capture enrolment is outside this family")
                key = message["attempt_nonce"]
                identity = (pid, _identity(pid)[0])
                if message.get("operation") == "release":
                    if descriptors:
                        raise FamilyError("Invalid capture release")
                    existing = self.registered.get(key)
                    if existing and existing[1] == identity:
                        os.close(self.registered.pop(key)[0])
                    continue
                if (
                    message.get("operation") != "register"
                    or len(descriptors) != 1
                    or message.get("boot_id") != self.boot
                    or not _nonce(message.get("request"))
                ):
                    raise FamilyError("Invalid capture enrolment request")
                backup = descriptors[0]
                info = os.fstat(backup)
                import fcntl

                writable = fcntl.fcntl(backup, fcntl.F_GETFL) & os.O_ACCMODE in (os.O_WRONLY, os.O_RDWR)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != uid
                    or info.st_mode & 0o077
                    or info.st_size
                    or info.st_nlink != 1
                    or not writable
                ):
                    raise FamilyError("Invalid capture backup descriptor")
                if key in self.registered or len(self.registered) >= MAX_ENROLMENTS:
                    raise FamilyError("Capture enrolment exceeds its bound")
                self.registered[key] = (backup, identity)
                descriptors.remove(backup)
                connection.send(
                    json.dumps({"request": message["request"], "accepted": True}).encode(), socket.MSG_DONTWAIT
                )
            except (OSError, ValueError, TypeError, KeyError, struct.error, FamilyError):
                if isinstance(message, dict) and _nonce(message.get("request")):
                    with suppress(OSError):
                        connection.send(
                            json.dumps({"request": message["request"], "accepted": False}).encode(), socket.MSG_DONTWAIT
                        )
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
                connection.close()
                self.connections.remove(connection)

    def complete(self):
        """Only the caller's exact root status + ECHILD authorize this write."""
        for attempt, (descriptor, _) in self.registered.items():
            if self.cleanup_deadline is not None and time.monotonic() >= self.cleanup_deadline:
                raise FamilyError("Capture backup publication exceeded cleanup bound")
            packet = {
                "schema": 1,
                "family_clean": True,
                "attempt_nonce": attempt,
                "ancestor_nonce": self.nonce,
                "boot_id": self.boot,
            }
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            encoded = json.dumps(packet).encode()
            if os.write(descriptor, encoded) != len(encoded):
                raise FamilyError("Capture backup write was incomplete")
            os.fsync(descriptor)

    def close(self):
        for descriptor, _ in self.registered.values():
            os.close(descriptor)
        self.registered.clear()
        for connection in self.connections:
            connection.close()
        self.connections.clear()
        if self.socket is not None:
            self.socket.close()
        if self.directory is not None:
            endpoint = self.directory / "control"
            endpoint.unlink(missing_ok=True)
            self.directory.rmdir()
            self.directory = None
