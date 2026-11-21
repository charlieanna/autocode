"""Record owned editor actions, then exec the real CLI with unchanged arguments."""

import json
import os
import sys
from pathlib import Path

import psutil

CLI = Path(__file__).resolve().parents[3] / "tools" / "autocode.py"
argv = sys.argv[1:]
if "--run-dir" in argv and "--status" not in argv:
    process = psutil.Process()
    record = (json.dumps({"pid": process.pid, "started": process.create_time()}) + "\n").encode()
    descriptor = os.open(os.environ["AUTOCODE_FIXTURE_RUSTUP_OWNERS"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, record)
    finally:
        os.close(descriptor)
os.execv(sys.executable, [sys.executable, str(CLI), *argv])
