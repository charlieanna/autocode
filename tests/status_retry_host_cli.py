"""Offline host-test CLI: inject an immediate timeout at the status observation seam."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import autocode_status_observation as observation  # noqa: E402

original = observation._observe


def record(message):
    target = os.environ.get("AUTOCODE_HOST_OBSERVATION_LOG")
    if target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(message + "\n")


if "--status" in sys.argv:
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    record(f"status {os.getpid()} marker={bool(marker and Path(marker).is_file())}")


def observe(operation, payload, *, timeout=None):
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    record(f"observe {operation} {os.getpid()}")
    if marker and Path(marker).is_file() and operation in ("job_retry", "failure_retry"):
        record(f"timeout {operation} {os.getpid()}")
        raise subprocess.TimeoutExpired("controlled-status-observation", 0)
    return original(operation, payload, timeout=timeout)


observation._observe = observe
sys.argv[0] = str(ROOT / "tools/autocode.py")
runpy.run_path(sys.argv[0], run_name="__main__")
