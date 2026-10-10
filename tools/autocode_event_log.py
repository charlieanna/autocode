"""Open a runner-owned event sink without allowing accidental pathname writes.

The provider inherits an already-open writable descriptor for stdout. The file
it can inspect for evidence is read-only, so treating that path as a report
output cannot truncate the transport stream. This is not an OS sandbox: a
process running as the same user could deliberately change file permissions or
replace the file. Providers must not modify runner-owned evidence.
"""

import os
from pathlib import Path


def open_events(path):
    """Create a private read-only log with a writable descriptor for the runner.

    Exclusive creation also refuses an existing log or symlink, preserving
    earlier attempts. POSIX permissions do not revoke an already-open fd.
    """
    descriptor = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise
