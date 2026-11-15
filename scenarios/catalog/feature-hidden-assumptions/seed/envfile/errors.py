"""How every envfile command reports a problem to the person running it."""

import sys

PROG = "envfile"


class EnvFileError(Exception):
    """A file that cannot be read as KEY=VALUE lines."""


def fail(message, code=2):
    """Print ``envfile: error: MESSAGE`` on stderr and exit with ``code``.

    Every command reports its errors through this helper, so they all look the
    same: 2 for bad input (a missing or malformed file), 1 for a lookup that
    found nothing.
    """
    print(f"{PROG}: error: {message}", file=sys.stderr)
    raise SystemExit(code)
