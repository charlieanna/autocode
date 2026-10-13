"""Linux setsid mediation; the independent keeper owns the notification listener.

Installing the filter is irreversible and belongs only in the blocked, single
threaded provider bootstrap. CONTINUE grants a syscall attempt, never proves it
succeeded. The keeper must persist prospective session ownership before replying.
"""

from __future__ import annotations

import array
import errno
import math
import os
import platform
import select
import socket
import struct
import sys


class BoundaryError(RuntimeError):
    pass


class UnsupportedBoundary(BoundaryError):
    pass


_ABIS = {
    "x86_64": (0xC000003E, 112, 317, 157),  # audit arch, setsid, seccomp, prctl
    "aarch64": (0xC00000B7, 157, 277, 167),
}
_NOTIF = struct.Struct("=QIIiIQ6Q")
_RESPONSE = struct.Struct("=QqiI")
_SIZES = (80, 24, 64)
_MAX_STATUS = 64 * 1024
_TRANSFER_PREFIX = b"autocode-session-boundary-v1:"
_RET_ERRNO = 0x00050000
_RET_NOTIFY = 0x7FC00000
_RET_ALLOW = 0x7FFF0000
_PRIVILEGED_CAPS = (1 << 19) | (1 << 21)  # CAP_SYS_PTRACE, CAP_SYS_ADMIN


def _self_status():
    try:
        with open("/proc/self/status", "rb") as status:
            data = status.read(_MAX_STATUS + 1)
    except OSError as error:
        raise UnsupportedBoundary("Cannot inspect the caller's Linux namespace/capabilities") from error
    namespace = [line.partition(b":")[2].split() for line in data.splitlines() if line.startswith(b"NSpid:")]
    if len(data) > _MAX_STATUS or namespace != [[str(os.getpid()).encode("ascii")]]:
        raise UnsupportedBoundary("Proc namespace does not match the session listener")
    return data


def _available_capabilities():
    data = _self_status()
    available = 0
    # NNP does not prevent capset from enabling capabilities already permitted.
    for field in (b"CapEff:", b"CapPrm:"):
        values = [line.partition(b":")[2].strip() for line in data.splitlines() if line.startswith(field)]
        if len(data) > _MAX_STATUS or len(values) != 1:
            raise UnsupportedBoundary("Cannot inspect the caller's Linux capabilities")
        try:
            available |= int(values[0], 16)
        except ValueError as error:
            raise UnsupportedBoundary("Invalid caller Linux capabilities") from error
    return available


def require_supported():
    """Refuse unqualified hosts/ABIs without loading libc or installing a filter."""
    abi = _ABIS.get(platform.machine())
    if sys.platform != "linux" or sys.byteorder != "little" or struct.calcsize("P") != 8 or abi is None:
        raise UnsupportedBoundary("Provider session ownership requires Linux native x86_64 or aarch64")
    if _available_capabilities() & _PRIVILEGED_CAPS:
        raise UnsupportedBoundary("Provider session ownership refuses CAP_SYS_PTRACE or CAP_SYS_ADMIN")
    return abi


def _libc():
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int
    return ctypes, libc


def _notification_sizes():
    _, _, seccomp, _ = require_supported()
    ctypes, libc = _libc()
    sizes = (ctypes.c_ushort * 3)()
    if libc.syscall(ctypes.c_long(seccomp), ctypes.c_uint(3), ctypes.c_uint(0), ctypes.byref(sizes)) < 0:
        error = ctypes.get_errno()
        raise UnsupportedBoundary(f"Cannot query seccomp notification ABI: {os.strerror(error)}")
    if tuple(sizes) != _SIZES:
        raise UnsupportedBoundary("Unqualified seccomp notification structure sizes")
    return tuple(sizes)


def _filter(abi):
    """Classic BPF, with explicit native ABI and immutable filter-install policy."""
    arch, setsid, seccomp, _ = abi
    instructions = []
    labels = {}

    def emit(code, jt=0, jf=0, value=0):
        instructions.append((code, jt, jf, value))

    def label(name):
        labels[name] = len(instructions)

    emit(0x20, value=4)  # BPF_LD | BPF_W | BPF_ABS: seccomp_data.arch
    emit(0x15, "native", "unsupported", arch)
    label("unsupported")
    emit(0x06, value=_RET_ERRNO | errno.ENOSYS)
    label("native")
    emit(0x20, value=0)  # syscall number
    if arch == _ABIS["x86_64"][0]:
        emit(0x45, "unsupported_x32", "native_nr", 0x40000000)
        label("unsupported_x32")
        emit(0x06, value=_RET_ERRNO | errno.ENOSYS)
        label("native_nr")
    emit(0x15, "notify", "test_seccomp", setsid)
    label("test_seccomp")
    emit(0x15, "seccomp", "allow", seccomp)
    label("seccomp")
    emit(0x20, value=16)  # args[0], low word (unsigned int operation)
    emit(0x15, "filter_flags", "allow", 1)  # SECCOMP_SET_MODE_FILTER
    label("filter_flags")
    emit(0x20, value=24)  # args[1], unsigned int flags
    emit(0x45, "deny", "allow", 8)  # SECCOMP_FILTER_FLAG_NEW_LISTENER
    label("notify")
    emit(0x06, value=_RET_NOTIFY)
    label("deny")
    # Listener release clears the kernel's duplicate-listener marker. Refusing
    # replacement listeners preserves mediation even after the keeper has died.
    # Ordinary filters cannot ALLOW over USER_NOTIF, and a later notifier with
    # no listener only returns ENOSYS; prctl cannot create notification listeners.
    emit(0x06, value=_RET_ERRNO | errno.EPERM)
    label("allow")
    emit(0x06, value=_RET_ALLOW)
    return [
        (
            code,
            labels[jt] - index - 1 if isinstance(jt, str) else jt,
            labels[jf] - index - 1 if isinstance(jf, str) else jf,
            value,
        )
        for index, (code, jt, jf, value) in enumerate(instructions)
    ]


def install():
    """Install an inherited listener in the sole-thread blocked bootstrap."""
    abi = require_supported()
    _notification_sizes()
    try:
        if os.listdir("/proc/self/task") != [str(os.getpid())]:
            raise UnsupportedBoundary("Session boundary installation requires a single threaded bootstrap")
    except OSError as error:
        raise UnsupportedBoundary("Cannot verify the bootstrap thread boundary") from error
    ctypes, libc = _libc()

    class SockFilter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class SockProgram(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]

    instructions = _filter(abi)
    native = (SockFilter * len(instructions))(*(SockFilter(*instruction) for instruction in instructions))
    program = SockProgram(len(native), native)
    if libc.prctl(ctypes.c_int(38), ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) < 0:
        raise UnsupportedBoundary(f"Cannot set no_new_privs: {os.strerror(ctypes.get_errno())}")
    fd = libc.syscall(ctypes.c_long(abi[2]), ctypes.c_uint(1), ctypes.c_uint(8), ctypes.byref(program))
    if fd < 0:
        raise UnsupportedBoundary(f"Cannot install seccomp session listener: {os.strerror(ctypes.get_errno())}")
    try:
        os.set_inheritable(fd, False)
    except OSError:
        os.close(fd)
        raise
    return fd


def guardian_harden():
    """Prevent a same-UID provider from duplicating the keeper's listener."""
    require_supported()
    ctypes, libc = _libc()
    if libc.prctl(ctypes.c_int(4), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) < 0:
        raise UnsupportedBoundary(f"Cannot make the session guardian non-dumpable: {os.strerror(ctypes.get_errno())}")


def listener_extinct(fd):
    """Only kernel POLLHUP proves no task retains this inherited filter."""
    if type(fd) is not int or fd < 0:
        raise BoundaryError("Invalid session listener descriptor")
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
    try:
        events = poller.poll(0)
    except OSError as error:
        raise BoundaryError("Cannot inspect session listener lifetime") from error
    result = False
    for returned_fd, event in events:
        if returned_fd != fd or event & (select.POLLERR | select.POLLNVAL):
            raise BoundaryError("Session listener lifetime is unknown")
        result |= bool(event & select.POLLHUP)
    return result


def _transfer_message(sock, nonce):
    if sock.family != socket.AF_UNIX or sock.type & 0xF not in (socket.SOCK_SEQPACKET, socket.SOCK_DGRAM):
        raise BoundaryError("Listener transfer requires a private Unix packet socket")
    if not isinstance(nonce, str) or not nonce or len(nonce) > 128:
        raise BoundaryError("Invalid listener transfer nonce")
    try:
        return _TRANSFER_PREFIX + nonce.encode("ascii")
    except UnicodeEncodeError as error:
        raise BoundaryError("Invalid listener transfer nonce") from error


def send_listener(sock, fd, nonce):
    """Transfer one descriptor; the sender retains its copy until closing it."""
    message = _transfer_message(sock, nonce)
    rights = array.array("i", [fd])
    if sock.sendmsg([message], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)]) != len(message):
        raise BoundaryError("Incomplete listener descriptor transfer")


def receive_listener(sock, nonce):
    """Receive exactly one nonce-bound descriptor, closing all rejected copies."""
    expected = _transfer_message(sock, nonce)
    size = array.array("i").itemsize
    message, ancillary, flags, _ = sock.recvmsg(
        256, socket.CMSG_SPACE(4 * size), getattr(socket, "MSG_CMSG_CLOEXEC", 0)
    )
    descriptors = []
    try:
        invalid = False
        for level, kind, data in ancillary:
            if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                invalid = True
                continue
            if len(data) % size:
                invalid = True
            rights = array.array("i")
            rights.frombytes(data[: len(data) - len(data) % size])
            descriptors.extend(rights)
        if invalid or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or message != expected or len(descriptors) != 1:
            raise BoundaryError("Invalid or unbound listener descriptor transfer")
        os.set_inheritable(descriptors[0], False)
        return descriptors.pop()
    finally:
        for fd in descriptors:
            os.close(fd)


def _ioctl(fd, number, payload, direction=3):
    import fcntl

    request = direction << 30 | len(payload) << 16 | ord("!") << 8 | number
    return fcntl.ioctl(fd, request, payload, True)


def receive_request(fd):
    """Receive one ready request; call only after polling the listener readable."""
    _notification_sizes()
    payload = bytearray(_NOTIF.size)
    try:
        _ioctl(fd, 0, payload)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.EINTR):
            return None
        raise BoundaryError(f"Cannot receive session request: {error}") from error
    cookie, tid, flags, nr, arch, instruction_pointer, *args = _NOTIF.unpack(payload)
    return {
        "listener_fd": fd,
        "id": cookie,
        "pid": tid,
        "flags": flags,
        "nr": nr,
        "arch": arch,
        "instruction_pointer": instruction_pointer,
        "args": args,
    }


def _cookie(cookie):
    if type(cookie) is not int or not 0 <= cookie < 1 << 64:
        raise BoundaryError("Invalid session request cookie")
    return cookie


def request_valid(fd, cookie):
    """Check that this exact kernel request still belongs to a blocked caller."""
    _notification_sizes()
    try:
        _ioctl(fd, 2, bytearray(struct.pack("=Q", _cookie(cookie))), direction=1)
        return True
    except OSError as error:
        if error.errno == errno.ENOENT:
            return False
        raise BoundaryError(f"Cannot validate session request: {error}") from error


def _respond(fd, cookie, error, flags):
    _notification_sizes()
    payload = bytearray(_RESPONSE.pack(_cookie(cookie), 0, error, flags))
    try:
        _ioctl(fd, 1, payload)
        return True
    except OSError as failure:
        if failure.errno == errno.ENOENT:
            return False
        raise BoundaryError(f"Cannot reply to session request: {failure}") from failure


def continue_request(fd, cookie):
    return _respond(fd, cookie, 0, 1)  # SECCOMP_USER_NOTIF_FLAG_CONTINUE


def reject_request(fd, cookie, error=errno.EPERM):
    if type(error) is not int or not 1 <= error <= 4095:
        raise BoundaryError("Invalid session request rejection errno")
    return _respond(fd, cookie, -error, 0)


def _thread_group(tid):
    # A proc mount from a different PID namespace cannot authenticate the TID
    # as viewed by this listener, even if an unrelated numeric PID happens to fit.
    if os.readlink("/proc/self") != str(os.getpid()):
        raise BoundaryError("Proc namespace does not match the session listener")
    _self_status()
    with open(f"/proc/{tid}/status", "rb") as status:
        data = status.read(_MAX_STATUS + 1)
    if len(data) > _MAX_STATUS:
        raise BoundaryError("Unbounded session caller metadata")
    fields = {}
    for line in data.splitlines():
        key, _, value = line.partition(b":")
        if key in (b"Pid", b"Tgid"):
            if key in fields or not value.strip().isdigit():
                raise BoundaryError("Invalid session caller metadata")
            fields[key] = int(value)
    if fields.get(b"Pid") != tid or fields.get(b"Tgid", 0) <= 0:
        raise BoundaryError("Cannot resolve the session caller thread group")
    return fields[b"Tgid"]


def caller_identity(request):
    """Resolve listener TID to fresh TGID birth metadata while its cookie is live."""
    arch, setsid, _, _ = require_supported()
    if (
        not isinstance(request, dict)
        or request.get("arch") != arch
        or request.get("nr") != setsid
        or request.get("flags") != 0
        or type(request.get("pid")) is not int
        or request["pid"] <= 0
    ):
        raise BoundaryError("Invalid native setsid notification")
    fd, cookie, tid = request["listener_fd"], request["id"], request["pid"]
    if not request_valid(fd, cookie):
        raise BoundaryError("Session caller disappeared before identity capture")
    try:
        from . import autocode_process as processes
    except ImportError:
        import autocode_process as processes
    try:
        tgid = _thread_group(tid)
        row = processes.process_table({tgid}).get(tgid)
        if _thread_group(tid) != tgid:
            raise BoundaryError("Session caller thread group changed during capture")
        if (
            not row
            or row.get("pid") != tgid
            or type(row.get("birth_identity")) not in (int, float)
            or not math.isfinite(row["birth_identity"])
            or row["birth_identity"] <= 0
        ):
            raise BoundaryError("Cannot capture session caller birth identity")
        if type(row.get("session")) is not int or row["session"] <= 0:
            raise BoundaryError("Cannot capture session caller native session")
    except (OSError, processes.ProcessError) as error:
        raise BoundaryError("Cannot inspect the blocked session caller") from error
    if not request_valid(fd, cookie):
        raise BoundaryError("Session caller disappeared during identity capture")
    return row
