"""Secret source names shared by tool containment and optional prompt attachments."""

import re
from pathlib import PurePosixPath

# The Seatbelt profile denies reading these names anywhere; bubblewrap masks them.
SECRET_NAME = re.compile(r"\.env(\..*)?|auth\.json|id_rsa|id_ed25519")


def secret_path(name):
    """A secret file or directory component must never be inlined as review source."""
    return any(SECRET_NAME.fullmatch(part) for part in PurePosixPath(name).parts)


# The identity a contained stage gives a file it is refused (#4246): it cannot read the bytes, and on
# Linux the name is a masked device, so only the name's presence is comparable on both sides.
WITHHELD = "withheld"


def withheld(name):
    """A file the contained stage's sandbox refuses to read: its own name matches SECRET_NAME."""
    return bool(SECRET_NAME.fullmatch(PurePosixPath(name).name))
