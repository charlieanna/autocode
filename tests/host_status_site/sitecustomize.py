"""Hold the selected observation worker past the real status deadline."""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


def record(line):
    target = os.environ.get("AUTOCODE_HOST_OBSERVATION_LOG")
    if target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(line + "\n")


def hold_worker():
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    selected = os.environ.get("AUTOCODE_HOST_OBSERVATION_OPERATION")
    if not marker or not Path(marker).is_file() or selected not in ("job_retry", "failure_retry"):
        return
    request = sys.stdin.read()
    sys.stdin = io.StringIO(request)
    if json.loads(request).get("operation") != selected:
        return
    record(f"child {os.getpid()} {psutil.Process().create_time()}")
    # The parent must terminate and collect this worker at its normal deadline.
    time.sleep(60)


def observe(operation, payload, *, timeout=None):
    try:
        return original(operation, payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        record(f"timeout {operation} {os.getpid()}")
        raise


if Path(sys.argv[0]).resolve() == ROOT / "tools/autocode_status_observation_worker.py":
    hold_worker()
else:
    import autocode_status_observation as observation

    original = observation._observe
    observation._observe = observe
