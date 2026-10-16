"""A run's development server (#730): its saved ``serve`` command, run on a free loopback port under supervision.

``autocode serve start`` (autocode_serve_cli) calls ``serve``, which runs in the foreground until it is asked to
stop (Ctrl-C or SIGTERM to it, or ``autocode serve stop``, which leaves a stop request beside the record) or the
server exits. The command runs in the run's workspace through the same shell and supervision as a test command
(autocode_command_supervision): its own session, credentials removed from the environment, ``PORT`` set to the
chosen port and ``HOST`` to 127.0.0.1, and a keeper that ends its process tree when ``serve`` ends or its process
dies. The record, ``dev-server.json`` in the run directory, is written only here, never in state.json, so the
run's controller and the server never save the same file. ``inspect`` reads it for the status view; a server
whose owner is gone reads as ``lost``. The server never starts, blocks or decides a stage.
"""

from __future__ import annotations

import contextlib
import http.client
import os
import signal
import socket
import subprocess
import uuid
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_command_supervision as command_supervision
    from . import autocode_dev_server_config as config
    from . import autocode_supervision as supervision
    from . import autocode_util as util
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_command_supervision as command_supervision
    import autocode_dev_server_config as config
    import autocode_supervision as supervision
    import autocode_util as util

RECORD = "dev-server.json"
STOP_REQUEST = "dev-server.stop"
LOCK = "dev-server.lock"
ATTEMPTS = "dev-server"  # one directory per start: output and supervision receipt
HOST = "127.0.0.1"
ACTIVE = ("starting", "running")
POLL_SECONDS = 0.1
STOP_SECONDS = 5  # between SIGTERM and SIGKILL to the server's process group
STOP_WAIT_SECONDS = 15  # how long `autocode serve stop` waits for the server to end
TAIL_CHARS = 4000
SHOWN = ("port", "started_at", "ready_at", "ended_at", "exit_code", "reason", "log")
STOPPED_BY_REQUEST = "stopped by autocode serve stop"
STOPPED_BY_SIGNAL = "stopped by Ctrl-C or a signal to autocode serve start"


class Busy(RuntimeError):
    """Another ``autocode serve start`` already serves this run."""


ERRORS = (Busy, supervision.SupervisionError)


def read(run_dir) -> dict | None:
    """The saved record, or None when there is none or it cannot be read."""
    try:
        value = util.read(Path(run_dir) / RECORD)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("schema") == 1 else None


def inspect(run_dir, settings) -> dict:
    """The status view's ``dev_server``: whether the run has a serve command, and its server's last record. The url
    is shown only while the server answers; a starting or running server whose owner is gone reads as ``lost``."""
    saved = settings.get(config.SETTING) if isinstance(settings, dict) else None
    configured = isinstance(saved, dict)
    record = read(run_dir)
    if record is None:
        return {
            "configured": configured,
            "status": "off",
            "url": None,
            "command": saved.get("command") if isinstance(saved, dict) else None,
            **dict.fromkeys(SHOWN),
        }
    status = record.get("status")
    if status in ACTIVE and not _owner_alive(record):
        status = "lost"
    return {
        "configured": configured,
        "status": status,
        "url": record.get("url") if status == "running" else None,
        "command": record.get("command"),
        **{key: record.get(key) for key in SHOWN},
    }


def serve(run_dir, workspace, saved, *, should_stop=lambda: False, on_ready=None, environ=None) -> dict:
    """Run ``saved`` (autocode_dev_server_config.checked) until it is asked to stop or exits; return its last record.

    ``on_ready(record)`` is called once the server answers. Raises Busy when this run already has a server."""
    run_dir = Path(run_dir)
    with (run_dir / LOCK).open("a+") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError:
            current = inspect(run_dir, {})
            where = f" at {current['url']}" if current["url"] else ""
            raise Busy(f"This run's development server is already {current['status']}{where}") from None
        try:
            return _serve(run_dir, Path(workspace), saved, should_stop, on_ready, environ)
        finally:
            util.unlock_file(handle)


def request_stop(run_dir, *, timeout=STOP_WAIT_SECONDS) -> dict | None:
    """Ask the run's server to stop and wait for its record to end; None when no server is running."""
    run_dir = Path(run_dir)
    record = read(run_dir)
    if record is None or record.get("status") not in ACTIVE or not _owner_alive(record):
        return None
    util.atomic_json(run_dir / STOP_REQUEST, {"id": record["id"], "requested_at": util.now()})
    deadline = util.monotonic() + timeout
    while util.monotonic() < deadline:
        current = read(run_dir)
        if current is None or current.get("id") != record["id"] or current.get("status") not in ACTIVE:
            return current
        if not _owner_alive(current):
            return {**current, "status": "lost"}
        util.sleep(POLL_SECONDS)
    raise TimeoutError(f"The development server did not stop within {timeout:g} s")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def _serve(run_dir: Path, workspace: Path, saved, should_stop, on_ready, environ) -> dict:
    with contextlib.suppress(FileNotFoundError):
        (run_dir / STOP_REQUEST).unlink()
    port = free_port()
    attempt = run_dir / ATTEMPTS / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    log = attempt / "output.log"
    record = {
        "schema": 1,
        "id": attempt.name,
        "status": "starting",
        "command": config.command_for(saved, port),
        "port": port,
        "url": f"http://{HOST}:{port}/",
        "cwd": str(workspace),
        "ready_path": saved.get("ready_path"),
        "ready_seconds": saved["ready_seconds"],
        "started_at": util.now(),
        "ready_at": None,
        "ended_at": None,
        "exit_code": None,
        "reason": None,
        "log": str(log),
        "tail": "",
        "supervision": None,
    }
    env = {**agent_env.scrubbed(os.environ if environ is None else environ), "PORT": str(port), "HOST": HOST}

    def admit(metadata):
        nonlocal record
        record = _save(run_dir, record, supervision=metadata)

    def stopping():
        if _stop_requested(run_dir, record["id"]):
            return STOPPED_BY_REQUEST
        return STOPPED_BY_SIGNAL if should_stop() else None

    child = None
    outcome: tuple[str, str | None] = ("failed", None)
    try:
        with (
            log.open("xb") as output,
            supervision.launch_command(
                command_supervision.shell_argv(record["command"]),
                receipt_path=attempt / "supervision.json",
                timeout=None,
                checkpoint=admit,
                cwd=str(workspace),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            ) as child,
        ):
            try:
                outcome = _wait_ready(child, port, saved, stopping)
                if outcome[0] == "running":
                    record = _save(run_dir, record, status="running", ready_at=util.now())
                    if on_ready is not None:
                        on_ready(dict(record))
                    reason = None
                    while child.poll() is None and not (reason := stopping()):
                        util.sleep(POLL_SECONDS)
                    outcome = ("exited", "the server exited") if reason is None else ("stopped", reason)
            finally:
                _end(child)
    except supervision.SupervisionError as error:
        if record["supervision"] is None:
            raise
        outcome = ("failed", f"supervision ended the server: {error}")
    except BaseException:
        if record["supervision"] is not None:
            _finish(run_dir, record, child, log, "stopped", STOPPED_BY_SIGNAL)
        raise
    return _finish(run_dir, record, child, log, *outcome)


def _wait_ready(child, port, saved, stopping):
    deadline = util.monotonic() + saved["ready_seconds"]
    while True:
        if child.poll() is not None:
            return "exited", "the server exited before it answered"
        reason = stopping()
        if reason:
            return "stopped", reason
        if _answers(port, saved.get("ready_path")):
            return "running", None
        if util.monotonic() >= deadline:
            where = saved.get("ready_path") or f"port {port}"
            return "failed", f"the server did not answer within {saved['ready_seconds']} s at {where}"
        util.sleep(POLL_SECONDS)


def _answers(port, path) -> bool:
    try:
        if path is None:
            with socket.create_connection((HOST, port), timeout=1):
                return True
        connection = http.client.HTTPConnection(HOST, port, timeout=2)
        try:
            connection.request("GET", path)
            return connection.getresponse().status < 400
        finally:
            connection.close()
    except (OSError, http.client.HTTPException):
        return False


def _owner_alive(record) -> bool:
    """False only when the saved owner, the ``autocode serve start`` process, is known to be gone; access that
    is denied reads as alive, since the keeper still owns the tree."""
    owner = supervision.observe(record.get("supervision") or {}).get("owner")
    return isinstance(owner, dict) and not (owner.get("checked") and owner.get("alive") is False)


def _stop_requested(run_dir: Path, ident) -> bool:
    try:
        return util.read(run_dir / STOP_REQUEST).get("id") == ident
    except (OSError, ValueError, AttributeError):
        return False


def _end(child) -> None:
    """Stop the server's process group while its leader is still ours to signal; the keeper then ends any
    process that left the group."""
    if child.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(STOP_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(STOP_SECONDS)


def _save(run_dir: Path, record: dict, **changes) -> dict:
    """The record with ``changes``, written atomically; a new dict, so a reader never sees a half-made one."""
    value = {**record, **changes}
    util.atomic_json(run_dir / RECORD, value)
    return value


def _finish(run_dir: Path, record: dict, child, log: Path, status: str, reason) -> dict:
    ended = _save(
        run_dir,
        record,
        status=status,
        reason=reason,
        ended_at=util.now(),
        exit_code=child.returncode if child is not None else None,
        tail=_tail(log),
    )
    with contextlib.suppress(FileNotFoundError):
        (run_dir / STOP_REQUEST).unlink()
    return ended


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - TAIL_CHARS))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""
