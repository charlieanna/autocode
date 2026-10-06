"""Exec a provider only after its independent keeper has been armed."""
from __future__ import annotations

import json
import os
import select
import sys


def main():
    release = int(sys.argv[1])
    command = json.loads(sys.argv[2])
    # Only the controller owns the write end. EOF before release means it died;
    # none of the provider's code or credentials has been used yet.
    try:
        if not select.select([release], [], [], 15)[0] or os.read(release, 1) != b'G':
            return 126
    finally:
        os.close(release)
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    raise SystemExit(main())
