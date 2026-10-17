"""A run's development server (#730): its saved ``serve`` command, run on a free loopback port under supervision.

``autocode serve start`` (autocode_serve_cli) calls ``serve``, which runs in the foreground until it is asked to
stop: Ctrl-C or SIGTERM to it, or ``autocode serve stop``, which leaves a stop request beside the record. It watches
the run's state.json, read-only: each new code checkpoint (an applied code-changing step, autocode_code_checkpoints)
restarts the server, on the same port when it can be bound again. A pause or the run's completion leaves it
running, so the person can try the app then. When the server exits or never answers, ``serve`` waits for the next
code change to start it again; once the run has completed, nothing will change the code, so ``serve`` ends instead.

The command runs in the run's workspace through the same shell and supervision as a test command
(autocode_command_supervision): its own session, credentials removed from the environment, ``PORT`` set to the
chosen port and ``HOST`` to 127.0.0.1, and a keeper that ends its process tree when ``serve`` ends or its process
dies. The record, ``dev-server.json`` in the run directory, is written only here, never in state.json, so the run's
controller and the server never save the same file. ``inspect`` reads it for the status view; a server whose owner
is gone reads as ``lost``. The server never starts, blocks or decides a stage.
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
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

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
RUN_STATE = "state.json"
STOP_REQUEST = "dev-server.stop"
LOCK = "dev-server.lock"
ATTEMPTS = "dev-server"  # one directory per start: output and supervision receipt
HOST = "127.0.0.1"
ACTIVE = ("starting", "running")
FINISHED = ("TASK_COMPLETE", "COMPLETE")  # autocode_run_view.COMPLETE: no step will change the code again
POLL_SECONDS = 0.1
STOP_SECONDS = 5  # between SIGTERM and SIGKILL to the server's process group
STOP_WAIT_SECONDS = 15  # how long `autocode serve stop` waits for the server to end
TAIL_CHARS = 4000
SHOWN = ("port", "started_at", "ready_at", "ended_at", "exit_code", "reason", "log", "restarts", "code_checkpoint")
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
            "waiting": False,
            **dict.fromkeys(SHOWN),
        }
    status = record.get("status")
    alive = _owner_alive(record)
    if status in ACTIVE and not alive:
        status = "lost"
    return {
        "configured": configured,
        "status": status,
        "url": record.get("url") if status == "running" else None,
        "command": record.get("command"),
        "waiting": bool(record.get("waiting")) and alive,
        **{key: record.get(key) for key in SHOWN},
    }


def serve(run_dir, workspace, saved, *, should_stop=lambda: False, on_ready=None, on_wait=None, environ=None) -> dict:
    """Run ``saved`` (autocode_dev_server_config.checked) until it is asked to stop; return its last record.

    ``on_ready(record)`` is called each time the server answers, and ``on_wait(record)`` each time it ends by
    itself while the run can still change its code. Raises Busy when this run already has a server."""
    run_dir = Path(run_dir)
    with (run_dir / LOCK).open("a+") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError:
            current = inspect(run_dir, {})
            where = f" at {current['url']}" if current["url"] else ""
            raise Busy(f"This run's development server is already {current['status']}{where}") from None
        try:
            return _serve(run_dir, Path(workspace), saved, should_stop, on_ready, on_wait, environ)
        finally:
            with contextlib.suppress(FileNotFoundError):
                (run_dir / STOP_REQUEST).unlink()
            util.unlock_file(handle)


def request_stop(run_dir, *, timeout=STOP_WAIT_SECONDS) -> dict | None:
    """Ask the run's server to stop and wait for its record to end; None when no server is running."""
    run_dir = Path(run_dir)
    record = read(run_dir)
    if record is None or not _serving(record) or not _owner_alive(record):
        return None
    session = record.get("session")
    util.atomic_json(run_dir / STOP_REQUEST, {"id": record["id"], "session": session, "requested_at": util.now()})
    deadline = util.monotonic() + timeout
    while util.monotonic() < deadline:
        current = read(run_dir)
        if current is None or current.get("session") != session or not _serving(current):
            return current
        if not _owner_alive(current):
            return {**current, "status": "lost"}
        util.sleep(POLL_SECONDS)
    raise TimeoutError(f"The development server did not stop within {timeout:g} s")


def code_version(state: RunState) -> str | None:
    """The run's latest applied code change: the id of its last code checkpoint. A check saved later on the same
    step updates that row in place, so it does not count as a change."""
    rows = state.get("code_checkpoints") if isinstance(state, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[-1], dict):
        return None
    return rows[-1].get("id")


def can_change_code(state: RunState) -> bool:
    """Whether a later step of the run may still change its code: it has a status and has not completed."""
    status = state.get("status") if isinstance(state, dict) else None
    return isinstance(status, str) and bool(status) and status not in FINISHED


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


class _RunWatch:
    """The run's state.json, parsed again only when the file is replaced or changes; never written here."""

    def __init__(self, path: Path):
        self.path = path
        self.stamp: tuple | None = None
        self.state: RunState = {}

    def current(self) -> RunState:
        try:
            stat = self.path.stat()
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if stamp != self.stamp:
                value = util.read(self.path)
                self.state, self.stamp = cast("RunState", value if isinstance(value, dict) else {}), stamp
        except (OSError, ValueError):
            pass  # keep the last state read; the runner replaces the file atomically
        return self.state


def _serve(run_dir: Path, workspace: Path, saved, should_stop, on_ready, on_wait, environ) -> dict:
    with contextlib.suppress(FileNotFoundError):
        (run_dir / STOP_REQUEST).unlink()
    watch = _RunWatch(run_dir / RUN_STATE)
    session = uuid.uuid4().hex
    previous = None
    while True:
        record, restart = _attempt(run_dir, workspace, saved, session, previous, watch, should_stop, on_ready, environ)
        if not restart:
            if not record["waiting"]:
                return record
            if on_wait is not None:
                on_wait(dict(record))
            ended = _wait_for_change(run_dir, record, watch, should_stop)
            if ended is not None:
                return ended
        previous = record


def _wait_for_change(run_dir: Path, record: dict, watch, should_stop) -> dict | None:
    """While the server is down, wait for the run's next code change (None) or for the end of ``serve``: a stop
    request, or the run completing (its final record)."""
    while True:
        reason = _stop_reason(run_dir, record["session"], should_stop)
        if reason:
            return _save(run_dir, record, status="stopped", reason=reason, waiting=False)
        state = watch.current()
        if code_version(state) != record["code_checkpoint"]:
            return None
        if not can_change_code(state):
            return _save(run_dir, record, waiting=False)
        util.sleep(POLL_SECONDS)


def _attempt(run_dir: Path, workspace: Path, saved, session, previous, watch, should_stop, on_ready, environ):
    """One start of the server; returns its record and whether a code change asked for a restart."""
    port = _port(previous["port"] if previous and previous.get("ready_at") else None)
    attempt = run_dir / ATTEMPTS / uuid.uuid4().hex
    attempt.mkdir(parents=True)
    log = attempt / "output.log"
    record = {
        "schema": 1,
        "id": attempt.name,
        "session": session,
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
        "restarts": previous["restarts"] + 1 if previous else 0,
        "code_checkpoint": code_version(watch.current()),
        "waiting": False,
    }
    env = {**agent_env.scrubbed(os.environ if environ is None else environ), "PORT": str(port), "HOST": HOST}

    def admit(metadata):
        nonlocal record
        record = _save(run_dir, record, supervision=metadata)

    def interrupt():
        reason = _stop_reason(run_dir, session, should_stop)
        if reason:
            return "stopped", reason
        return ("restart", None) if code_version(watch.current()) != record["code_checkpoint"] else None

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
                outcome = _wait_ready(child, port, saved, interrupt)
                if outcome[0] == "running":
                    record = _save(run_dir, record, status="running", ready_at=util.now())
                    if on_ready is not None:
                        on_ready(dict(record))
                    interrupted = None
                    while child.poll() is None and not (interrupted := interrupt()):
                        util.sleep(POLL_SECONDS)
                    outcome = interrupted or ("exited", "the server exited")
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
    if outcome[0] == "restart":
        return record, True
    waiting = outcome[0] != "stopped" and can_change_code(watch.current())
    return _finish(run_dir, record, child, log, *outcome, waiting=waiting), False


def _wait_ready(child, port, saved, interrupt):
    deadline = util.monotonic() + saved["ready_seconds"]
    while True:
        if child.poll() is not None:
            return "exited", "the server exited before it answered"
        interrupted = interrupt()
        if interrupted:
            return interrupted
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


def _port(previous) -> int:
    """``previous`` when it can be bound again, as the servers that dev tools start do (SO_REUSEADDR), so the
    address stays the same across restarts; otherwise a free port."""
    if previous is not None:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with contextlib.suppress(OSError):
                probe.bind((HOST, previous))
                return previous
    return free_port()


def _serving(record) -> bool:
    """Whether the record's ``serve`` still runs: its server starts or answers, or it waits to start it again."""
    return record.get("status") in ACTIVE or bool(record.get("waiting"))


def _stop_reason(run_dir: Path, session, should_stop) -> str | None:
    if _stop_requested(run_dir, session):
        return STOPPED_BY_REQUEST
    return STOPPED_BY_SIGNAL if should_stop() else None


def _stop_requested(run_dir: Path, session) -> bool:
    try:
        return util.read(run_dir / STOP_REQUEST).get("session") == session
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


def _finish(run_dir: Path, record: dict, child, log: Path, status: str, reason, *, waiting=False) -> dict:
    """The attempt's last record; ``waiting`` when ``serve`` goes on to wait for the next code change."""
    return _save(
        run_dir,
        record,
        status=status,
        reason=reason,
        ended_at=util.now(),
        exit_code=child.returncode if child is not None else None,
        tail=_tail(log),
        waiting=waiting,
    )


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - TAIL_CHARS))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""
