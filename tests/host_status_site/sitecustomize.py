"""Inject a deterministic status timeout in test-owned CLI and warm-host processes."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import autocode_status_observation as observation  # noqa: E402

original = observation._observe


def observe(operation, payload, *, timeout=None):
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    target = os.environ.get("AUTOCODE_HOST_OBSERVATION_LOG")
    if target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(f"observe {operation} {os.getpid()}\n")
    if marker and Path(marker).is_file() and operation in ("job_retry", "failure_retry"):
        if target:
            with Path(target).open("a", encoding="utf-8") as output:
                output.write(f"timeout {operation} {os.getpid()}\n")
        raise subprocess.TimeoutExpired("controlled-status-observation", 0)
    return original(operation, payload, timeout=timeout)


observation._observe = observe
