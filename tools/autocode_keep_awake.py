"""Keep a Mac awake while a controller drives a run (#1717).

A live run on a laptop stopped for a person: the Mac idled to sleep mid-stream (on battery, 25%), the
controller could not enforce its idle limit while suspended, and on wake the provider connection reset.
While a controller drives a run it holds macOS's idle-sleep assertion through ``caffeinate -i -w PID``,
which ends by itself when this process exits, however it exits. A closed lid or a low battery still
sleeps; autocode_network_retry recovers a read-only stage from the reset that follows.
``AUTOCODE_KEEP_AWAKE=0`` turns it off. Elsewhere this does nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

SETTING = "AUTOCODE_KEEP_AWAKE"
_held: dict = {}


def hold() -> bool:
    """Hold the idle-sleep assertion for this process's lifetime; True when it is held."""
    if _held.get("pid") == os.getpid():
        return True
    if sys.platform != "darwin" or os.environ.get(SETTING) == "0":
        return False
    executable = shutil.which("caffeinate", path="/usr/bin:/bin")
    if not executable:
        return False
    try:
        subprocess.Popen(
            [executable, "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        return False
    _held["pid"] = os.getpid()
    return True
