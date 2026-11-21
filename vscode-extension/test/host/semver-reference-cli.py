"""Forward public CLI calls and record owned advancing process outcomes."""

import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psutil

CLI = Path(__file__).resolve().parents[3] / "tools/autocode.py"
argv = sys.argv[1:]
owners = Path(os.environ["AUTOCODE_SEMVER_OWNERS"])


def record_owner(process):
    row = (json.dumps({"pid": process.pid, "started": process.create_time()}) + "\n").encode()
    fd = os.open(owners, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, row)
    finally:
        os.close(fd)


record_owner(psutil.Process())
if "--status" in argv:
    os.execv(sys.executable, [sys.executable, str(CLI), *argv])
child = subprocess.Popen([sys.executable, str(CLI), *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
with contextlib.suppress(psutil.NoSuchProcess):
    record_owner(psutil.Process(child.pid))
stdout, stderr = child.communicate()
safe_argv = []
redact_next = False
for arg in argv:
    if redact_next:
        safe_argv.append("<redacted>")
        redact_next = False
    elif arg in {
        "--resolver-token",
        "--approve-goal",
        "--review-token",
        "--expected-token",
        "--expected-recovery-token",
    }:
        safe_argv.append(arg)
        redact_next = True
    elif any(
        arg.startswith(flag + "=")
        for flag in (
            "--resolver-token",
            "--approve-goal",
            "--review-token",
            "--expected-token",
            "--expected-recovery-token",
        )
    ):
        safe_argv.append(arg.split("=", 1)[0] + "=<redacted>")
    else:
        safe_argv.append(arg)


def redact_output(value):
    text = value.decode("utf-8", "replace")
    text = re.sub(r"\br\d+:[a-f0-9]{64}\b", "<plan-token>", text)
    return re.sub(r"(--(?:resolver|review|expected|expected-recovery)-token(?:=|\s+))[^\s]+", r"\1<redacted>", text)


record = {
    "argv": safe_argv,
    "kind": "approve_plan" if "--approve-goal" in argv else "other",
    "returncode": child.returncode,
    "stdout": redact_output(stdout),
    "stderr": redact_output(stderr),
}
with Path(os.environ["AUTOCODE_SEMVER_ACTIONS"]).open("a") as log:
    log.write(json.dumps(record) + "\n")
sys.stdout.buffer.write(stdout)
sys.stderr.buffer.write(stderr)
raise SystemExit(child.returncode)
