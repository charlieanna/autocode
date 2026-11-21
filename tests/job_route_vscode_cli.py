"""Forward the real CLI and retain non-secret action diagnostics for the editor fixture."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "tools/autocode.py"


def busy_observation(stderr: str):
    """Best-effort post-refusal process sample; no process details grant authority."""
    match = re.search(r"Existing run process ([0-9]+) is active; leave it untouched", stderr)
    if match is None:
        return None
    pid = int(match.group(1))
    try:
        import psutil
    except ImportError:
        return {"pid": pid, "sampled_after_refusal": True, "identity": "unavailable"}
    try:
        process = psutil.Process(pid)
        started = process.create_time()
        argv = process.cmdline()
        script = next((i for i, item in enumerate(argv) if Path(item).name == "autocode.py"), None)
        status_at = next((i for i, item in enumerate(argv) if item == "--status"), None)
        order = (
            "absent"
            if status_at is None
            else "unknown"
            if script is None
            else "first"
            if status_at == script + 1
            else "later"
        )
        return {
            "pid": pid,
            "sampled_after_refusal": True,
            "started": started,
            "argv_sha256": hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
            "script": "autocode.py" if script is not None else "other",
            "status_option": order,
        }
    except (psutil.Error, OSError, ValueError, IndexError):
        return {"pid": pid, "sampled_after_refusal": True, "identity": "unavailable"}


def main() -> int:
    args = sys.argv[1:]
    is_answer = "--answer" in args
    answer = args[args.index("--answer") + 1] if is_answer and args.index("--answer") + 1 < len(args) else ""
    log_path = Path(os.environ["AUTOCODE_ROUTE_HOST_ACTIONS"]) if is_answer else None
    if log_path:
        with log_path.open("a") as log:
            log.write(json.dumps({"event": "started", "answer": answer}) + "\n")
    result = subprocess.run([sys.executable, str(CLI), *args], capture_output=True)
    if log_path:
        stderr = result.stderr.decode(errors="replace")
        # The token is passed by the extension in its environment, never recorded.
        with log_path.open("a") as log:
            log.write(
                json.dumps(
                    {
                        "event": "finished",
                        "answer": answer,
                        "exit_code": result.returncode,
                        "stderr": re.sub(r"jr:[0-9a-f]+", "jr:[redacted]", stderr)[-2000:],
                        "busy_process_sample": busy_observation(stderr),
                    }
                )
                + "\n"
            )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
