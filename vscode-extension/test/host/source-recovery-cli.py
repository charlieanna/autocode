"""Forward the real CLI, briefly holding the named source-recovery action.

The host test inspects the editor while the actual action is in flight, then
releases it. No status output or argument is altered and no live model is used.
"""

import os
import select
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[3] / "tools" / "autocode.py"


def main(argv: list[str]) -> int:
    if "--resume-paused" in argv:
        release = os.open(os.environ["AUTOCODE_SOURCE_ACTION_RELEASE"], os.O_RDWR | os.O_NONBLOCK)
        try:
            Path(os.environ["AUTOCODE_SOURCE_ACTION_STARTED"]).write_text("\n".join(argv))
            if not select.select([release], [], [], 90)[0]:
                return 124
            os.read(release, 1)
        finally:
            os.close(release)
        result = subprocess.call([sys.executable, str(CLI), *argv])
        Path(os.environ["AUTOCODE_SOURCE_ACTION_FINISHED"]).write_text(str(result))
        return result
    result = subprocess.call([sys.executable, str(CLI), *argv])
    if "--status" in argv:
        with Path(os.environ["AUTOCODE_SOURCE_STATUS_READS"]).open("a") as reads:
            reads.write(f"{result}\n")
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
