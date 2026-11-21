"""Forward the real CLI and retain non-secret action diagnostics for the editor fixture."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "tools/autocode.py"


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
        # The token is passed by the extension in its environment, never recorded.
        with log_path.open("a") as log:
            log.write(
                json.dumps(
                    {
                        "event": "finished",
                        "answer": answer,
                        "exit_code": result.returncode,
                        "stderr": re.sub(r"jr:[0-9a-f]+", "jr:[redacted]", result.stderr.decode(errors="replace"))[
                            -2000:
                        ],
                    }
                )
                + "\n"
            )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
