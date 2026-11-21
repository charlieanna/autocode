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
    result = subprocess.run([sys.executable, str(CLI), *args], capture_output=True)
    if "--answer" in args:
        # The token is passed by the extension in its environment, not recorded here.
        answer = args[args.index("--answer") + 1] if args.index("--answer") + 1 < len(args) else ""
        with Path(os.environ["AUTOCODE_ROUTE_HOST_ACTIONS"]).open("a") as log:
            log.write(
                json.dumps(
                    {
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
