"""Offline status host CLI with logs for its owned readers."""

import os
import runpy
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
if "--status" in sys.argv:
    target = os.environ.get("AUTOCODE_HOST_OBSERVATION_LOG")
    if target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(f"start {os.getpid()} {psutil.Process().create_time()}\n")
sys.argv[0] = str(ROOT / "tools/autocode.py")
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    if "--status" in sys.argv and target:
        with Path(target).open("a", encoding="utf-8") as output:
            output.write(f"end {os.getpid()}\n")
