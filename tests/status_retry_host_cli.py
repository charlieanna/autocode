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


def observe(operation, payload, *, timeout=None):
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    if marker and Path(marker).is_file() and operation in ("job_retry", "failure_retry"):
        with Path(os.environ["AUTOCODE_HOST_OBSERVATION_LOG"]).open("a", encoding="utf-8") as output:
            output.write(f"{operation} {os.getpid()}\n")
        raise subprocess.TimeoutExpired("controlled-status-observation", 0)
    return original(operation, payload, timeout=timeout)


observation._observe = observe
sys.argv[0] = str(ROOT / "tools/autocode.py")
runpy.run_path(sys.argv[0], run_name="__main__")
