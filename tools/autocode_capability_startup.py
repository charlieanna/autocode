"""Reduce Linux CLI privileges before run state or provider ownership is touched."""

from __future__ import annotations

import ctypes
import os
import sys

OPT_OUT = "AUTOCODE_NO_CAPABILITY_DROP"
REEXEC = "_AUTOCODE_CAPABILITY_REEXEC"
DROPPED = False
_CAPABILITIES = (19, 21)  # CAP_SYS_PTRACE, CAP_SYS_ADMIN
_MASK = sum(1 << cap for cap in _CAPABILITIES)


def capabilities() -> dict[str, int]:
    """Read actual kernel sets; incomplete observations cannot establish a drop."""
    with open("/proc/self/status", encoding="ascii") as status:
        lines = status.read(65537).splitlines()
    result = {}
    for field in ("CapBnd", "CapPrm", "CapEff"):
        values = [line.partition(":")[2].strip() for line in lines if line.startswith(field + ":")]
        if len(values) != 1:
            raise ValueError(f"Cannot inspect Linux {field}")
        result[field] = int(values[0], 16)
    return result


def drop_bounding(mask: int) -> None:
    """Irreversibly remove only the prohibited bounding bits in this process."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    for cap in _CAPABILITIES:
        if mask & (1 << cap) and libc.prctl(24, cap, 0, 0, 0) < 0:  # PR_CAPBSET_DROP
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))


def prepare() -> None:
    """Re-exec once to clear effective/permitted privileges after a bounding drop.

    Any failure leaves the existing session admission check in charge. A receipt
    is display-only: neither it nor this helper grants provider launch authority.
    """
    global DROPPED
    DROPPED = False
    if sys.platform != "linux" or os.environ.get(OPT_OUT) == "1":
        return
    receipt = os.environ.pop(REEXEC, None)
    try:
        sets = capabilities()
        if receipt:
            if receipt == "1" and not any(value & _MASK for value in sets.values()):
                DROPPED = True
                print("autocode: dropped CAP_SYS_PTRACE and CAP_SYS_ADMIN for this invocation", file=sys.stderr)
            return
        mask = sets["CapBnd"] & _MASK
        if mask:
            drop_bounding(mask)
            os.execve(sys.executable, [sys.executable, *sys.orig_argv[1:]], dict(os.environ, **{REEXEC: "1"}))
    except (OSError, ValueError, AttributeError):
        return
