"""Secret source names shared by tool containment and optional prompt attachments."""

import re
from pathlib import PurePosixPath

# The Seatbelt profile denies reading these names anywhere; bubblewrap masks them.
SECRET_NAME = re.compile(r"\.env(\..*)?|auth\.json|id_rsa|id_ed25519")


def secret_path(name):
    """A secret file or directory component must never be inlined as review source."""
    return any(SECRET_NAME.fullmatch(part) for part in PurePosixPath(name).parts)
