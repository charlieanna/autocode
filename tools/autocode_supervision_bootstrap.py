"""Exec a provider only after its independent keeper has been armed."""

from __future__ import annotations

import errno
import json
import os
import select
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from . import autocode_session_boundary as boundary
except ImportError:
    import autocode_session_boundary as boundary


def main():
    release = int(sys.argv[1])
    command = json.loads(sys.argv[2])
    channel = socket.socket(fileno=int(sys.argv[3]))
    nonce = sys.argv[4]
    listener = None
    try:
        listener = boundary.install()
        boundary.send_listener(channel, listener, nonce)
        os.close(listener)
        listener = None  # only the independent keeper now owns the listener
        # The keeper witnesses this request and qualifies CONTINUE support.
        # This admitted process already leads its session, so the real syscall
        # must fail EPERM; merely getting EPERM without a notification is no proof.
        try:
            os.setsid()
        except PermissionError as error:
            if error.errno != errno.EPERM:
                raise
            channel.send(("verified:" + nonce).encode())
        else:
            raise boundary.BoundaryError("Session boundary probe changed the admitted session")
    except (boundary.BoundaryError, OSError) as error:
        print(f"Provider session boundary: {error}", file=sys.stderr, flush=True)
        return 126
    finally:
        if listener is not None:
            os.close(listener)
        channel.close()
    # Only the controller owns the write end. EOF before release means it died;
    # none of the provider's code or credentials has been used yet.
    try:
        if not select.select([release], [], [], 15)[0] or os.read(release, 1) != b"G":
            return 126
    finally:
        os.close(release)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
