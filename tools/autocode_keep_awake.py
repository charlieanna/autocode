"""Keep a Mac awake while a controller drives a run (#1717).

A live run on a laptop stopped for a person: the Mac idled to sleep mid-stream (on battery, 25%), the
controller could not enforce its idle limit while suspended, and on wake the provider connection reset.
While a controller drives a run it holds macOS's ``PreventUserIdleSystemSleep`` power assertion, taken in
this process through IOKit: the kernel releases it when the process exits, however it exits, and no child
process exists to outlive it. (The first version spawned ``caffeinate -w PID``; a supervised CLI's keeper
found that child alive at discharge and SIGKILLed the CLI.) A closed lid or a low battery still sleeps;
autocode_network_retry recovers a read-only stage from the reset that follows. ``AUTOCODE_KEEP_AWAKE=0``
turns it off. Elsewhere this does nothing.
"""

from __future__ import annotations

import ctypes
import os
import sys

SETTING = "AUTOCODE_KEEP_AWAKE"
IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
UTF8 = 0x08000100  # kCFStringEncodingUTF8
LEVEL_ON = 255  # kIOPMAssertionLevelOn
_held: dict = {}


def hold() -> bool:
    """Hold the idle-sleep assertion for this process's lifetime; True when it is held."""
    if _held.get("pid") == os.getpid():
        return True
    if sys.platform != "darwin" or os.environ.get(SETTING) == "0":
        return False
    assertion = _assert("PreventUserIdleSystemSleep", "AutoCode is driving a run")
    if assertion is None:
        return False
    _held.update(pid=os.getpid(), assertion=assertion)
    return True


def _assert(kind: str, reason: str):
    try:
        iokit = ctypes.cdll.LoadLibrary(IOKIT)
        cf = ctypes.cdll.LoadLibrary(CORE_FOUNDATION)
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int
        iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        name = cf.CFStringCreateWithCString(None, kind.encode(), UTF8)
        label = cf.CFStringCreateWithCString(None, reason.encode(), UTF8)
        assertion = ctypes.c_uint32(0)
        if not name or not label:
            return None
        if iokit.IOPMAssertionCreateWithName(name, LEVEL_ON, label, ctypes.byref(assertion)) != 0:
            return None
        return assertion.value
    except (OSError, AttributeError):
        return None
