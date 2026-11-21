"""Offline host-test CLI; test-only sitecustomize injects an observation timeout."""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
if "--status" in sys.argv:
    target = os.environ.get("AUTOCODE_HOST_OBSERVATION_LOG")
    marker = os.environ.get("AUTOCODE_HOST_OBSERVATION_TIMEOUT")
    if target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(f"status {os.getpid()} marker={bool(marker and Path(marker).is_file())}\n")
sys.argv[0] = str(ROOT / "tools/autocode.py")
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    if "--status" in sys.argv and target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(f"end {os.getpid()}\n")
