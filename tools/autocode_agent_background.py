"""The agent step's long work, in a process of its own, so no agent call waits for a model (#3984).

An agent's tool call has a time limit (an MCP client's tool timeout, a shell tool's
command limit); a run's work to its next stop does not. autocode_agent_step therefore
saves a decision in the call itself and hands the work after it, starting a run or
advancing it to the next stop, to a detached process. The call returns at once, and
``status`` reports whether that process still works.

The process runs the agent step's own drive (``autocode_agent_step --background-drive
REQUEST``), which reaches the run only through the task-run interface. This module
holds no AutoCode logic: it writes the request, starts the process, and reads three
small files the step itself owns, under ``workspace/.autocode/agent-step``:

    RUN.request.json  what to do, written before the process starts (the current launch_id)
    RUN.launch.json   the process id of that launch, written once it started
    RUN.result.json   how the launch ended, written by the process when it ends

A launch is ``working`` while its process lives and has written no result, ``stopped``
or ``failed`` once it has, and ``lost`` when its process is gone without a result. A
lost launch is reported, never repeated: the run's own status view says what follows.
Only the standard library is used.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

RECORDS = Path(".autocode") / "agent-step"
RUNS = Path(".autocode") / "runs"
# How often a waiting status call looks again.
POLL_SECONDS = 0.5

# Starts argv detached with env, its output in log; returns the process id (None when it already ended).
Spawn = Callable[[list[str], dict[str, str], Path], "int | None"]


def fresh_run_dir(workspace: Path) -> Path:
    """A new run's directory: an immediate child of the workspace's runs, as the task-run start requires."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return workspace / RUNS / f"{stamp}-agent-{secrets.token_hex(3)}"


def launch(workspace: Path, run_dir: Path, request: dict, argv: list[str], env: dict[str, str], spawn: Spawn) -> str:
    """Record ``request`` for ``run_dir`` and start ``argv`` plus the request's path; returns the launch id."""
    folder = _folder(workspace)
    launch_id = secrets.token_hex(8)
    request_path = _path(folder, run_dir, "request")
    _write(request_path, {**request, "launch_id": launch_id, "workspace": str(workspace), "run_dir": str(run_dir)})
    pid = spawn([*argv, str(request_path)], env, _path(folder, run_dir, "log"))
    if _read(_path(folder, run_dir, "result")).get("launch_id") != launch_id:
        _write(_path(folder, run_dir, "launch"), {"launch_id": launch_id, "pid": pid, "started_at": time.time()})
    return launch_id


def detached(argv: list[str], env: dict[str, str], log: Path) -> int:
    """Start ``argv`` in its own session, so neither the agent's call nor its server's exit ends it."""
    with open(log, "ab") as output:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, env=env, start_new_session=True
        )
    # A long-lived caller (the MCP server) reaps it when it ends, so a finished process is not seen as alive.
    threading.Thread(target=child.wait, name="autocode-agent-step-reap", daemon=True).start()
    return child.pid


def read_request(path: Path) -> dict:
    request = _read(path)
    if not request.get("launch_id"):
        raise ValueError(f"{path} holds no agent-step request")
    return request


def finish(request: dict, outcome: dict) -> None:
    """The launch's own process records how it ended."""
    folder = _folder(Path(request["workspace"]))
    _write(
        _path(folder, Path(request["run_dir"]), "result"),
        {**outcome, "launch_id": request["launch_id"], "finished_at": time.time()},
    )


def state(workspace: Path, run_dir: Path) -> dict | None:
    """The latest launch for ``run_dir``: ``{state, operation, error}``, or None when the step launched none."""
    folder = workspace / RECORDS
    request = _read(_path(folder, run_dir, "request"))
    launch_id = request.get("launch_id")
    if not launch_id:
        return None
    found = {"operation": request.get("operation"), "error": None}
    result = _read(_path(folder, run_dir, "result"))
    if result.get("launch_id") == launch_id:
        return {**found, "state": result.get("state") or "failed", "error": result.get("error")}
    started = _read(_path(folder, run_dir, "launch"))
    if started.get("launch_id") != launch_id or _alive(started.get("pid")):
        return {**found, "state": "working"}  # not yet recorded means its launch call is still returning
    if _read(_path(folder, run_dir, "result")).get("launch_id") == launch_id:  # it ended as we looked
        return state(workspace, run_dir)
    return {
        **found,
        "state": "lost",
        "error": "the background process ended without recording how; the run's status says what follows",
    }


def wait(workspace: Path, run_dir: Path, seconds: float, *, sleep=time.sleep, clock=time.monotonic) -> dict | None:
    """``state``, after waiting up to ``seconds`` for a working launch to end."""
    deadline = clock() + max(0.0, seconds)
    current = state(workspace, run_dir)
    while current and current["state"] == "working" and clock() < deadline:
        sleep(min(POLL_SECONDS, max(0.0, deadline - clock())))
        current = state(workspace, run_dir)
    return current


def launched_runs(workspace: Path) -> list[Path]:
    """The run directories this step launched work for, including a start whose run is not saved yet."""
    folder = workspace / RECORDS
    if not folder.is_dir():
        return []
    named = (_read(path).get("run_dir") for path in sorted(folder.glob("*.request.json")))
    return [Path(run_dir) for run_dir in named if run_dir]


def working(workspace: Path) -> list[Path]:
    return [
        run_dir for run_dir in launched_runs(workspace) if (state(workspace, run_dir) or {}).get("state") == "working"
    ]


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _folder(workspace: Path) -> Path:
    folder = workspace / RECORDS
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _path(folder: Path, run_dir: Path, kind: str) -> Path:
    return folder / f"{run_dir.name}.{kind}.json" if kind != "log" else folder / f"{run_dir.name}.log"


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # the request holds the brief
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    os.replace(temporary, path)
