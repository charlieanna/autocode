"""Forward the real CLI, briefly holding the named source-recovery action.

The host test inspects the editor while the actual action is in flight, then
releases it. No status output or argument is altered and no live model is used.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

CLI = Path(__file__).resolve().parents[3] / "tools" / "autocode.py"


def main(argv: list[str]) -> int:
    if "--resume-paused" in argv:
        Path(os.environ["AUTOCODE_SOURCE_ACTION_STARTED"]).write_text("\n".join(argv))
        release = Path(os.environ["AUTOCODE_SOURCE_ACTION_RELEASE"])
        deadline = time.monotonic() + 90
        while not release.exists():
            if time.monotonic() >= deadline:
                return 124
            time.sleep(0.1)
        result = subprocess.call([sys.executable, str(CLI), *argv])
        Path(os.environ["AUTOCODE_SOURCE_ACTION_FINISHED"]).write_text(str(result))
        return result
    return subprocess.call([sys.executable, str(CLI), *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
