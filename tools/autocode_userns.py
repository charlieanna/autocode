"""A bubblewrap user namespace that keeps the caller's access to files (#4152 phase 3).

``bwrap --unshare-user`` maps only the caller's own id into the namespace it creates. For an
ordinary user that changes nothing a command can read. For root it takes away what root reads
through its capabilities: inside, a file another user owns belongs to an unmapped id, so
CAP_DAC_OVERRIDE no longer applies to it, and root can no longer enter, say, another user's 0750
home directory. A proof command that CI's demo ran under sudo could not start its own interpreter,
which lived in the runner's home, and the demo stopped at ``PAUSED_INVALID_OUTPUT``.

So for root, ``bubblewrap`` starts bubblewrap through this module run as a script. The script
creates the user namespace itself and has a short-lived child map every id to the same id (root
holds CAP_SETUID and CAP_SETGID, which lets it write such a map), then runs bubblewrap as root
inside the namespace, where bubblewrap needs no namespace of its own. The command keeps the
capabilities a child of this process gets anyway. Bubblewrap drops each one missing from this
process's bounding set: AutoCode's CLI drops CAP_SYS_PTRACE and CAP_SYS_ADMIN from a root runner
(``autocode_capability_startup``), so the command cannot undo the namespace's mounts. An ordinary
user still gets ``bwrap --unshare-user``.

Imports nothing from AutoCode.
"""

from __future__ import annotations

import ctypes
import os
import sys

CLONE_NEWUSER = 0x10000000
# Every id the initial namespace has, mapped to itself.
IDENTITY = "0 0 4294967295\n"
# The capabilities bubblewrap knows by name. A capability numbered above these that this process lacks
# is not dropped by name, since an older bubblewrap would refuse the unknown name.
CAPABILITIES = [
    "CHOWN",
    "DAC_OVERRIDE",
    "DAC_READ_SEARCH",
    "FOWNER",
    "FSETID",
    "KILL",
    "SETGID",
    "SETUID",
    "SETPCAP",
    "LINUX_IMMUTABLE",
    "NET_BIND_SERVICE",
    "NET_BROADCAST",
    "NET_ADMIN",
    "NET_RAW",
    "IPC_LOCK",
    "IPC_OWNER",
    "SYS_MODULE",
    "SYS_RAWIO",
    "SYS_CHROOT",
    "SYS_PTRACE",
    "SYS_PACCT",
    "SYS_ADMIN",
    "SYS_BOOT",
    "SYS_NICE",
    "SYS_RESOURCE",
    "SYS_TIME",
    "SYS_TTY_CONFIG",
    "MKNOD",
    "LEASE",
    "AUDIT_WRITE",
    "AUDIT_CONTROL",
    "SETFCAP",
    "MAC_OVERRIDE",
    "MAC_ADMIN",
    "SYSLOG",
    "WAKE_ALARM",
    "BLOCK_SUSPEND",
    "AUDIT_READ",
]


def bounding(status="/proc/self/status"):
    """This process's capability bounding set, as a bit mask, or None when it cannot be read."""
    try:
        with open(status, encoding="ascii") as lines:
            for line in lines:
                if line.startswith("CapBnd:"):
                    return int(line.partition(":")[2].strip(), 16)
    except (OSError, ValueError):
        return None
    return None


def capability_flags(mask):
    """bubblewrap flags that give the command every capability in ``mask`` and no other one it can name."""
    if mask is None:
        return ["--cap-drop", "ALL"]
    dropped = [f"CAP_{name}" for number, name in enumerate(CAPABILITIES) if not mask & (1 << number)]
    return ["--cap-add", "ALL", *(flag for name in dropped for flag in ("--cap-drop", name))]


def bubblewrap(bwrap, *, euid=None, mask=None):
    """The start of a bubblewrap command line whose namespace keeps the caller's access to files."""
    euid = os.geteuid() if euid is None else euid
    if euid != 0:
        return [bwrap, "--unshare-user"]
    mask = bounding() if mask is None else mask
    return [sys.executable, "-I", os.path.abspath(__file__), bwrap, *capability_flags(mask)]


def _fail(message):
    print(f"autocode_userns: {message}", file=sys.stderr)
    return 126


def main(argv):
    """Enter a user namespace that maps every id to itself, then run ``argv`` (bubblewrap) in it."""
    if not argv:
        return _fail("no command to run")
    ready, go = os.pipe()
    answer, done = os.pipe()
    parent = os.getpid()
    child = os.fork()
    if child == 0:
        os.close(go)
        os.close(answer)
        result = b"ok"
        if os.read(ready, 1) != b"g":
            os._exit(0)
        try:
            for name in ("uid_map", "gid_map"):
                with open(f"/proc/{parent}/{name}", "w", encoding="ascii") as mapping:
                    mapping.write(IDENTITY)
        except OSError as error:
            result = f"could not write the namespace's {name}: {error}".encode()[:400]
        os.write(done, result)
        os._exit(0)
    os.close(ready)
    os.close(done)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(CLONE_NEWUSER) != 0:
        errno = ctypes.get_errno()
        os.close(go)
        os.waitpid(child, 0)
        return _fail(f"could not create a user namespace: {os.strerror(errno)}")
    os.write(go, b"g")
    os.close(go)
    result = os.read(answer, 400)
    os.waitpid(child, 0)
    if result != b"ok":
        return _fail(result.decode(errors="replace") or "the namespace's id map was not written")
    try:
        os.execvp(argv[0], argv)
    except OSError as error:
        return _fail(f"could not run {argv[0]}: {error}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
