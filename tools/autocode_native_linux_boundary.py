"""Linux kernel plans for original-owner readonly checks and image acquisition.

This is the native visual owner's boundary, not another provider/controller or
the general OpenCode tool backend. A plan grants no task or image acceptance.
Callers must seal its exact bytes, retain their original process owner and run
the kernel conformance controls before releasing an approved target.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from pathlib import Path

BWRAP = "/usr/bin/bwrap"
VERSION = "native-linux-bwrap-v1"
SYSTEM_DIRECTORIES = ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64", "/usr/share", "/etc/fonts")
SYSTEM_ALIASES = ("/bin", "/sbin", "/lib", "/lib64")
SYSTEM_FILES = ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache")
DENIED_SYSCALLS = (
    "bind",
    "listen",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "pidfd_getfd",
    "bpf",
    "perf_event_open",
    "keyctl",
    "add_key",
    "request_key",
    "kexec_load",
    "open_by_handle_at",
    "io_uring_setup",
    "mount",
    "umount2",
    "setns",
    "unshare",
    "pivot_root",
)


def _require(condition, reason):
    if not condition:
        raise ValueError("Native Linux boundary: " + reason)


def _path(value):
    path = Path(value)
    _require(
        path.is_absolute()
        and str(path) == os.fspath(value)
        and ".." not in path.parts
        and "\0" not in str(path)
        and path.resolve(strict=True) == path,
        "authority path is missing, relative or aliased",
    )
    mode = path.lstat().st_mode
    _require(stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISSOCK(mode), "unsupported authority file kind")
    return path


def executable():
    _require(sys.platform == "linux", "Linux is required")
    path = _path(BWRAP)
    info = path.stat()
    _require(
        stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o022 == 0 and os.access(path, os.X_OK),
        "bubblewrap must be an administrator-owned executable",
    )
    return path


def seccomp_program(*, network="none", inherited_listener=None, deny_fork=False):
    """Compile native-architecture filter bytes; never install them here.

    Both modes deny binding/listening and kernel/namespace escape operations.
    A capture/check also denies every new connection. The separately confined
    Kilo client may connect only to its explicitly mounted Unix source relay;
    a private network namespace provides no route to the host or Internet.
    """
    executable()
    _require(type(deny_fork) is bool, "fork restriction must be an explicit boolean")
    _require(network in ("none", "unix-relay"), "unknown network authority")
    _require(
        inherited_listener is None or type(inherited_listener) is int and inherited_listener >= 3,
        "invalid original listener descriptor",
    )
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.seccomp_export_bpf.restype = ctypes.c_int
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; explicit denials below.
    _require(context, "cannot allocate seccomp filter")
    try:
        names = tuple(name for name in DENIED_SYSCALLS if name != "listen" or inherited_listener is None)
        names += ("connect",) if network == "none" else ()
        for name in names:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            _require(number >= 0, "kernel syscall identity is unavailable: " + name)
            result = library.seccomp_rule_add_array(context, 0x00050000 | errno.EACCES, number, 0, None)
            _require(result == 0, "cannot deny syscall: " + name)
        if inherited_listener is not None or deny_fork or network == "unix-relay":

            class Comparison(ctypes.Structure):
                _fields_ = [
                    ("arg", ctypes.c_uint),
                    ("op", ctypes.c_int),
                    ("datum_a", ctypes.c_uint64),
                    ("datum_b", ctypes.c_uint64),
                ]

        if network == "unix-relay":
            # A source relay needs AF_UNIX only. Refuse Internet, netlink and
            # packet socket creation with an actual permission error; a private
            # network namespace alone would return reachability errors instead.
            number = library.seccomp_syscall_resolve_name(b"socket")
            comparison = Comparison(0, 1, 1, 0)  # NE: domain != AF_UNIX
            _require(
                number >= 0
                and library.seccomp_rule_add_array(
                    context, 0x00050000 | errno.EACCES, number, 1, ctypes.byref(comparison)
                )
                == 0,
                "cannot restrict socket family to the original Unix relay",
            )
        if deny_fork:
            # A Bun worker thread is allowed, but a process clone is not.
            # clone3 already returns ENOSYS below, forcing the filterable clone.
            for name in (b"fork", b"vfork"):
                number = library.seccomp_syscall_resolve_name(name)
                if number >= 0:
                    _require(
                        library.seccomp_rule_add_array(context, 0x00050000 | errno.EACCES, number, 0, None) == 0,
                        "cannot deny process fork",
                    )
            number = library.seccomp_syscall_resolve_name(b"clone")
            comparison = Comparison(0, 7, 0x00010000, 0)  # MASKED_EQ: no CLONE_THREAD
            _require(
                number >= 0
                and library.seccomp_rule_add_array(
                    context, 0x00050000 | errno.EACCES, number, 1, ctypes.byref(comparison)
                )
                == 0,
                "cannot deny process clone",
            )
        if inherited_listener is not None:
            number = library.seccomp_syscall_resolve_name(b"listen")
            _require(number >= 0, "listen syscall identity unavailable")
            # Node/libuv repeats listen() when adopting an already-listening
            # inherited socket. ERRNO(0) returns success WITHOUT executing it.
            # Even closing/reusing this FD can therefore never open a new port.
            for comparison, error in ((4, 0), (1, errno.EACCES)):  # EQ, NE
                argument = Comparison(0, comparison, inherited_listener, 0)
                _require(
                    library.seccomp_rule_add_array(context, 0x00050000 | error, number, 1, ctypes.byref(argument)) == 0,
                    "cannot constrain inherited listen adoption",
                )
        # Newer libc can fall back to clone for ordinary threads. Deny clone3
        # with ENOSYS so no uninspected extensible clone argument escapes.
        number = library.seccomp_syscall_resolve_name(b"clone3")
        _require(number >= 0, "clone3 identity unavailable")
        _require(
            library.seccomp_rule_add_array(context, 0x00050000 | errno.ENOSYS, number, 0, None) == 0,
            "cannot constrain clone3",
        )
        descriptor = os.memfd_create("autocode-native-seccomp", os.MFD_CLOEXEC)
        try:
            _require(library.seccomp_export_bpf(context, descriptor) == 0, "cannot export actual filter")
            size = os.fstat(descriptor).st_size
            _require(0 < size <= 65536 and size % 8 == 0, "invalid BPF program size")
            return os.pread(descriptor, size, 0)
        finally:
            os.close(descriptor)
    finally:
        library.seccomp_release(context)


def seccomp_filter(path, *, network="none", inherited_listener=None, deny_fork=False):
    """Write one fresh immutable filter; never replace an earlier receipt."""
    path = Path(path)
    _require(
        path.is_absolute()
        and path.parent.resolve(strict=True) == path.parent
        and not path.exists()
        and not path.is_symlink(),
        "filter needs a fresh physical output slot",
    )
    program = seccomp_program(network=network, inherited_listener=inherited_listener, deny_fork=deny_fork)
    with path.open("xb") as output:
        output.write(program)
    path.chmod(0o400)
    return path


def arguments(*, read_paths=(), write_paths=(), hidden_paths=(), cwd, seccomp_fd, inherited_fds=()):
    """Exact mount plan; no shell parsing, process launch or task acceptance.

    Hidden directories cover earlier read mounts and can then expose exact
    later child exceptions. Every declared path already exists: bwrap cannot
    create an unapproved host path. Only write_paths become writable mounts.
    """
    executable()
    _require(type(seccomp_fd) is int and seccomp_fd >= 3, "original filter descriptor required")
    _require(
        all(type(fd) is int and fd >= 3 and fd != seccomp_fd for fd in inherited_fds)
        and len(set(inherited_fds)) == len(inherited_fds),
        "inherited descriptors are invalid or repeated",
    )
    reads, writes, hidden = (tuple(_path(item) for item in paths) for paths in (read_paths, write_paths, hidden_paths))
    _require(not set(reads) & set(writes), "same path cannot have two authorities")
    _require(all(path != Path("/") for path in (*reads, *writes, *hidden)), "root cannot be caller authority")
    work = _path(cwd)
    _require(work.is_dir(), "working directory must exist")
    _require(any(work.is_relative_to(path) for path in (*reads, *writes)), "working directory is not readable")
    for path in writes:
        _require(not path.is_file() or path.stat().st_nlink == 1, "writable file has shared inode authority")
    for path in hidden:
        _require(
            path.is_dir() and any(path.is_relative_to(parent) for parent in reads), "hidden path lacks a read mount"
        )
        _require(not any(path.is_relative_to(parent) for parent in writes), "cannot hide within writable authority")
    argv = [
        "--unshare-all",
        "--cap-drop",
        "ALL",
        "--die-with-parent",
        # The existing original-owner supervisor owns the session boundary.
        # No --new-session can escape its retained process-group authority.
    ]
    shown = []
    for name in SYSTEM_DIRECTORIES:
        path = Path(name)
        if path.is_dir() and path.resolve() == path:
            argv.extend(("--ro-bind", name, name))
            shown.append(path)
    for name in SYSTEM_ALIASES:
        path = Path(name)
        if path.is_symlink():
            argv.extend(("--symlink", os.readlink(path), name))
        elif path.is_dir():
            argv.extend(("--ro-bind", name, name))
            shown.append(path)
    for name in SYSTEM_FILES:
        path = Path(name)
        if path.is_file() and path.resolve() == path:
            argv.extend(("--ro-bind", name, name))
    argv.extend(("--proc", "/proc", "--dev", "/dev"))
    mounts = [(path, "read") for path in reads if not any(path.is_relative_to(base) for base in shown)]
    mounts += [(path, "write") for path in writes] + [(path, "hidden") for path in hidden]
    _require(len({path for path, _ in mounts}) == len(mounts), "duplicate mount authority")
    for path, kind in sorted(mounts, key=lambda row: (len(row[0].parts), str(row[0]))):
        if kind == "hidden":
            argv.extend(("--tmpfs", str(path)))
        else:
            argv.extend(("--bind" if kind == "write" else "--ro-bind", str(path), str(path)))
    # Mount child exceptions before making the temporary masks read-only.
    # The original launcher closes every descriptor except its pass_fds;
    # bwrap consumes its seccomp descriptor and retains the inherited channels.
    for path in hidden:
        argv.extend(("--remount-ro", str(path)))
    argv.extend(("--seccomp", str(seccomp_fd), "--chdir", str(work), "--remount-ro", "/"))
    return argv
