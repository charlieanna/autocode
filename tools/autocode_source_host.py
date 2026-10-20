"""A long-lived helper that answers source snapshots from its own memory (#1707, stage 1).

Every AutoCode process fingerprinted the whole workspace again: a chat on idlecampus (13.6k files,
117 MB) spent 2.5-3 s of each status refresh and each turn on it, because content hashes are kept
only in a process's memory (#711), never in a file a stage could rewrite. This host is that memory,
kept alive between processes: it runs ``autocode_util.snapshot`` itself, with its own
``ContentHashes``, and returns the result over a private Unix socket. The answer is the same
function's answer; nothing about it is written to disk.

Opt-in while it is proven: ``AUTOCODE_SOURCE_HOST=1`` in the environment of the AutoCode process
(``install``). A process that cannot reach a host starts one and takes its own snapshot that time; a
host that fails or is slow is never waited on again by that process. A host serves one code version
and one Git environment (the socket name is a digest of both), and exits after ``IDLE_SECONDS``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

SETTING = "AUTOCODE_SOURCE_HOST"
IDLE_SECONDS = 30 * 60
CONNECT_SECONDS = 0.5
ANSWER_SECONDS = 120.0
# Environment that can change what `git ls-files` and `git rev-parse` report.
GIT_ENV = ("HOME", "PATH", "XDG_CONFIG_HOME")
_state = {"failed": False, "started": False}


def enabled(env=None) -> bool:
    return (os.environ if env is None else env).get(SETTING) == "1"


def install() -> None:
    """Route this process's snapshots through the host, when the setting asks for it."""
    if enabled():
        util.use_snapshot_service(snapshot)


def socket_path(env=None) -> Path:
    env = os.environ if env is None else env
    code = b"".join(Path(str(module.__file__)).read_bytes() for module in (util, sys.modules[__name__]))
    git_env = sorted((key, value) for key, value in env.items() if key.startswith("GIT_") or key in GIT_ENV)
    name = hashlib.sha256(code + json.dumps(git_env).encode()).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"autocode-{os.getuid()}" / f"source-{name}.sock"


def snapshot(workspace, nested):
    """The host's snapshot of ``workspace``, or None when this process should take its own."""
    if _state["failed"]:
        return None
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(str(path))
            connection.settimeout(ANSWER_SECONDS)
            request = {"root": os.path.abspath(workspace), "nested": bool(nested)}
            connection.sendall(json.dumps(request).encode() + b"\n")
            answer = json.loads(_read_line(connection))
    except (FileNotFoundError, ConnectionRefusedError):
        _start(path)
        return None
    except (OSError, ValueError):
        _state["failed"] = True  # slow or broken: this process stops asking
        return None
    if "paused" in answer:
        raise util.Paused(answer["paused"]["status"], answer["paused"]["reason"])
    return answer.get("snapshot")


def _read_line(connection) -> bytes:
    chunks: list[bytes] = []
    while not chunks or not chunks[-1].endswith(b"\n"):
        chunk = connection.recv(1 << 20)
        if not chunk:
            raise OSError("the source host closed the connection")
        chunks.append(chunk)
    return b"".join(chunks)


def _start(path: Path) -> None:
    if _state["started"]:
        return
    _state["started"] = True
    with contextlib.suppress(OSError):
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
            return  # never serve through a folder another user could reach
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.touched = time.monotonic()
        try:
            request = json.loads(self.rfile.readline())
            answer = {"snapshot": util.snapshot(request["root"], request["nested"])}
        except util.Paused as paused:
            answer = {"paused": {"status": paused.status, "reason": str(paused)}}
        except Exception as error:  # noqa: BLE001 - any other failure: the caller takes its own snapshot
            answer = {"error": type(error).__name__}
        self.wfile.write(json.dumps(answer).encode() + b"\n")
        self.server.touched = time.monotonic()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    touched = 0.0


def serve(path: Path) -> None:
    """Answer snapshots on ``path`` until idle for ``IDLE_SECONDS``."""
    os.umask(0o077)
    with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(CONNECT_SECONDS)
        probe.connect(str(path))
        return  # another host already answers here
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    server = _Server(str(path), _Handler)
    server.touched = time.monotonic()

    def watch():
        while time.monotonic() - server.touched < IDLE_SECONDS:
            time.sleep(5)
        server.shutdown()

    threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()
        with contextlib.suppress(OSError):
            path.unlink()


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
