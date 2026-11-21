"""The AutoCode CLI as the Extension Host suite launches it, with a status read it can hold (#3860).

Every argument goes unchanged to this checkout's CLI. Each ``--status`` read is recorded in the
file ``AUTOCODE_HOST_READS`` names, as a ``start PID BIRTH`` line before it runs and an ``end PID CODE``
line after, so the suite can count the readers still outstanding. A read that starts while the file
``AUTOCODE_HOST_HOLD`` names exists records ``held PID`` and waits before it starts the CLI: the
read is held past the editor's deadline and released when the suite removes the file. Nothing else is
changed, and the read itself is the CLI's own. Its child PID/birth is also recorded so
cleanup cannot mistake wrapper exit for completion of the real CLI.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

CLI = Path(__file__).resolve().parents[3] / "tools" / "autocode.py"


def record(line: str) -> None:
    with open(os.environ["AUTOCODE_HOST_READS"], "a", encoding="utf-8") as reads:
        reads.write(line + "\n")


def main(argv: list[str]) -> int:
    if "--status" not in argv:
        return subprocess.call([sys.executable, str(CLI), *argv])
    record(f"start {os.getpid()} {psutil.Process().create_time()}")
    hold = Path(os.environ["AUTOCODE_HOST_HOLD"])
    if hold.exists():
        record(f"held {os.getpid()}")
    while hold.exists():
        time.sleep(0.1)
    child = subprocess.Popen([sys.executable, str(CLI), *argv])
    try:
        started = psutil.Process(child.pid).create_time()
    except psutil.NoSuchProcess:
        # A child already reaped needs no liveness observation.
        if child.poll() is None:
            raise
    else:
        record(f"child {child.pid} {started}")
    code = child.wait()
    record(f"end {os.getpid()} {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
