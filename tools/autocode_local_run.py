"""Run an integrated architecture's components together on this machine and smoke-check them.

`autocode components ARCH --integrate TARGET --run-local` (docs/task-lanes.md,
"Running the combined system locally") calls this after integrating. In order:

1. ``prepare``, before any component is built: every component declares a
   runtime block, at least one is a service, the runtime graph has a start
   order, and ARCHITECTURE/smoke.json is valid and talks only to services.
   ``check_docker`` then confirms Docker with Compose v2 and a reachable daemon.
2. ``LocalRun.run``, after integration: checks the combined tree holds each
   running component's directory (and Dockerfile), writes the Compose file
   (autocode_compose_file) into an AutoCode-owned directory, and starts the
   components one start_layers layer at a time with ``up --no-deps``, waiting
   for each layer to be ready before the next: a service when GET <health> on
   its loopback-published port answers 2xx, a component with a health command
   when Compose reports it "healthy", any other once it is running. Then it
   sends each smoke step to 127.0.0.1:<published port>, in order, stopping at
   the first that fails, and always tears the project down (``down -v
   --remove-orphans``), on failure and on KeyboardInterrupt too, unless asked
   to keep it running.

Every docker invocation goes through a ``Runner``, every HTTP request through an
``HttpClient`` and all waiting through a ``Clock``, so tests replace each and
never need Docker or real time. The defaults talk to 127.0.0.1 with
http.client, which uses no proxy and follows no redirect.

Depends only on the runtime-block and Compose-file modules and the workspace
helper; it never imports the build layer or the single-task runner.
"""
from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .autocode_component_runtime import (ComponentRuntime, SmokeCheck, SmokeStep, check_smoke, load_smoke,
                                             render_step, require_runnable, start_layers)
    from .autocode_compose_file import compose_document, render
    from .autocode_workspaces import keep_out_of_git
except ImportError:
    from autocode_component_runtime import (ComponentRuntime, SmokeCheck, SmokeStep, check_smoke, load_smoke,
                                            render_step, require_runnable, start_layers)
    from autocode_compose_file import compose_document, render
    from autocode_workspaces import keep_out_of_git

# How long a layer may take to become ready once started; --health-timeout overrides it.
HEALTH_TIMEOUT = 120.0
POLL_INTERVAL = 1.0
PROBE_TIMEOUT = 2.0
STEP_TIMEOUT = 30.0
# `up --build` builds images, which can take a while the first time.
UP_TIMEOUT = 1800.0
COMMAND_TIMEOUT = 120.0
LOG_LINES = 50
MAX_RESPONSE = 1 << 20
SHOWN_BODY = 300
# Where compose files go, under the workspace's own (git-ignored) .autocode-components/.
LOCAL_RUN_DIR = ("local-run",)


class DockerUnavailable(RuntimeError):
    """docker, Compose v2 or the Docker daemon cannot be used."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list, float], CommandResult]


def subprocess_runner(argv: list, timeout: float) -> CommandResult:
    """Run ``argv`` and capture its output. A missing executable raises DockerUnavailable;
    a command that runs past ``timeout`` is a failure with exit status 124."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise DockerUnavailable(f"{argv[0]} is not installed or not on PATH; --run-local needs Docker with "
                                f"Compose v2 (docker compose)") from None
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout.decode(errors="replace") if isinstance(expired.stdout, bytes) else expired.stdout or ""
        err = expired.stderr.decode(errors="replace") if isinstance(expired.stderr, bytes) else expired.stderr or ""
        return CommandResult(124, out, err + f"\n{' '.join(argv[:3])} ... did not finish within {timeout:g} s")
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


class Clock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class HttpClient:
    """Plain HTTP to 127.0.0.1: http.client uses no proxy and follows no redirect.
    Raises OSError (or http.client.HTTPException) when no response arrives."""

    def request(self, method: str, port: int, path: str, body: bytes | None, timeout: float) -> HttpResponse:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return HttpResponse(response.status, response.read(MAX_RESPONSE))
        finally:
            connection.close()


@dataclass(frozen=True)
class LocalRunPlan:
    """What --run-local will start and check, validated before anything is built."""
    runtimes: dict
    layers: list
    smoke: SmokeCheck


def prepare(architecture_dir, runtimes: Mapping[str, ComponentRuntime | None]) -> LocalRunPlan:
    """Validate everything --run-local needs that the architecture alone decides.
    ``runtimes`` maps every component id to its block (None when it has none).
    Raises ValueError naming what is missing."""
    runtimes = dict(runtimes)
    require_runnable(runtimes, runtimes)
    layers = start_layers(runtimes)
    smoke = load_smoke(architecture_dir)
    check_smoke(smoke, runtimes)
    return LocalRunPlan(runtimes=runtimes, layers=layers, smoke=smoke)


def check_docker(runner: Runner = subprocess_runner) -> None:
    """Raise DockerUnavailable unless docker, Compose v2 and the daemon all answer."""
    compose = runner(["docker", "compose", "version"], COMMAND_TIMEOUT)
    if compose.returncode != 0:
        raise DockerUnavailable(f"`docker compose version` failed; --run-local needs Compose v2 (the docker "
                                f"compose plugin): {_tail(compose.stderr or compose.stdout)}")
    server = runner(["docker", "version", "--format", "{{.Server.Version}}"], COMMAND_TIMEOUT)
    if server.returncode != 0:
        raise DockerUnavailable(f"the Docker daemon is not reachable (`docker version` failed); start Docker and "
                                f"try again: {_tail(server.stderr or server.stdout)}")


def check_tree(plan: LocalRunPlan, tree: Path) -> None:
    """The combined tree must hold what each running component is built from. Raises ValueError."""
    for cid, runtime in sorted(plan.runtimes.items()):
        if runtime is None or not runtime.runs:
            continue
        directory = tree / "components" / cid
        if not directory.is_dir():
            raise ValueError(f"{cid} cannot be started: {directory} does not exist in the integrated tree")
        if runtime.dockerfile is not None and not (directory / runtime.dockerfile).is_file():
            raise ValueError(f"{cid} cannot be started: its dockerfile {directory / runtime.dockerfile} does not "
                             f"exist in the integrated tree")


class _Failure(Exception):
    def __init__(self, component: str | None, detail: str, step: str | None = None):
        super().__init__(detail)
        self.component, self.detail, self.step = component, detail, step


@dataclass
class LocalRun:
    """One start, smoke check and teardown of the combined system in ``tree``."""
    plan: LocalRunPlan
    tree: Path
    workdir: Path  # AutoCode-owned directory; the compose file goes in workdir/<project>/
    runner: Runner = subprocess_runner
    http: HttpClient = field(default_factory=HttpClient)
    clock: Clock = field(default_factory=Clock)
    health_timeout: float = HEALTH_TIMEOUT
    keep_running: bool = False
    log_lines: int = LOG_LINES
    out: object = None  # where progress lines go; stderr by default
    project: str = field(default_factory=lambda: f"autocode-{uuid.uuid4().hex[:12]}")

    def __post_init__(self):
        self.compose_file = Path(self.workdir) / self.project / "compose.json"
        self.ports: dict[str, int] = {}

    def compose(self, *args: str) -> list:
        return ["docker", "compose", "-p", self.project, "-f", str(self.compose_file), *args]

    def run(self) -> dict:
        """Start, check and tear down. Returns the summary's local_run entry; its status
        is "passed" or "failed". A KeyboardInterrupt still tears down, then propagates."""
        summary = {"status": "failed", "project": self.project, "compose_file": str(self.compose_file),
                   "layers": self.plan.layers, "ready": [], "ports": self.ports, "steps": [],
                   "failed_component": None, "failed_step": None, "detail": None, "logs": None,
                   "torn_down": False, "stop_command": None}
        started = False
        try:
            check_tree(self.plan, self.tree)
            self._write_compose_file()
            for number, layer in enumerate(self.plan.layers, 1):
                self._say(f"starting layer {number} of {len(self.plan.layers)}: {', '.join(layer)}")
                started = True
                self._up(layer)
                self._wait_ready(layer, summary["ready"])
            captured: dict = {}
            for index, step in enumerate(self.plan.smoke.steps, 1):
                result = self._step(index, step, captured)
                summary["steps"].append(result)
                self._say(f"smoke step {index} ({step.name}): {'ok' if result['ok'] else 'FAILED'}")
                if not result["ok"]:
                    raise _Failure(step.service, f"smoke step {index} ({step.name}) failed: {result['detail']}",
                                   step=step.name)
            summary["status"] = "passed"
        except (_Failure, ValueError, DockerUnavailable) as failure:
            component = getattr(failure, "component", None)
            summary.update(failed_component=component, failed_step=getattr(failure, "step", None),
                           detail=getattr(failure, "detail", str(failure)))
            self._say(f"--run-local failed: {summary['detail']}")
            if component is not None and started:
                summary["logs"] = self._logs(component)
                self._say(f"last {self.log_lines} log lines of {component}:\n{summary['logs']}")
        finally:
            if started:
                if self.keep_running:
                    summary["stop_command"] = " ".join(self.compose("down", "-v", "--remove-orphans"))
                    self._say(f"left running; stop it with: {summary['stop_command']}")
                else:
                    summary["torn_down"] = self._down()
        return summary

    def _write_compose_file(self) -> None:
        self.compose_file.parent.mkdir(parents=True, exist_ok=True)
        self.compose_file.write_text(render(compose_document(self.plan.runtimes, self.tree)), encoding="utf-8")

    def _up(self, layer: list) -> None:
        result = self.runner(self.compose("up", "-d", "--build", "--no-deps", *layer), UP_TIMEOUT)
        if result.returncode != 0:
            raise _Failure(layer[0] if len(layer) == 1 else None,
                           f"docker compose up failed for {', '.join(layer)}: {_tail(result.stderr or result.stdout)}")

    def _down(self) -> bool:
        try:
            result = self.runner(self.compose("down", "-v", "--remove-orphans"), COMMAND_TIMEOUT)
        except DockerUnavailable as error:
            self._say(f"could not tear down {self.project}: {error}")
            return False
        if result.returncode != 0:
            self._say(f"could not tear down {self.project}: {_tail(result.stderr or result.stdout)}")
        return result.returncode == 0

    def _logs(self, component: str) -> str:
        try:
            result = self.runner(self.compose("logs", "--no-color", "--tail", str(self.log_lines), component),
                                 COMMAND_TIMEOUT)
        except DockerUnavailable as error:
            return str(error)
        return (result.stdout + result.stderr).rstrip("\n")

    def _states(self) -> dict:
        """Each container's (State, Health) by service, from `ps --all --format json`, which
        prints one JSON array (older Compose v2) or one object per line (newer)."""
        result = self.runner(self.compose("ps", "--all", "--format", "json"), COMMAND_TIMEOUT)
        if result.returncode != 0:
            raise _Failure(None, f"docker compose ps failed: {_tail(result.stderr or result.stdout)}")
        text = result.stdout.strip()
        try:
            rows = json.loads(text) if text.startswith("[") else [json.loads(line) for line in text.splitlines()
                                                                  if line.strip()]
        except ValueError:
            raise _Failure(None, f"docker compose ps printed something that is not JSON: {_tail(text)}") from None
        return {row.get("Service"): (str(row.get("State", "")).lower(), str(row.get("Health", "")).lower())
                for row in rows if isinstance(row, dict)}

    def _port(self, cid: str) -> int:
        if cid not in self.ports:
            runtime = self.plan.runtimes[cid]
            result = self.runner(self.compose("port", cid, str(runtime.port)), COMMAND_TIMEOUT)
            address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            host, _, port = address.rpartition(":")
            if result.returncode != 0 or not port.isdigit():
                raise _Failure(cid, f"cannot find the host port published for {cid}'s port {runtime.port}: "
                                    f"{_tail(result.stderr or address)}")
            self.ports[cid] = int(port)
        return self.ports[cid]

    def _wait_ready(self, layer: list, ready: list) -> None:
        deadline = self.clock.now() + self.health_timeout
        pending = list(layer)
        last: dict[str, str] = {}
        while True:
            states = self._states()
            for cid in list(pending):
                state, health = states.get(cid, ("missing", ""))
                if state in ("exited", "dead", "missing", "removing"):
                    raise _Failure(cid, f"{cid} is not running (state {state})")
                runtime = self.plan.runtimes[cid]
                if runtime.health_command:
                    if health == "unhealthy":
                        raise _Failure(cid, f"{cid}'s health command reports unhealthy")
                    done, last[cid] = health == "healthy", f"health {health or 'unknown'}"
                elif runtime.is_service:
                    done, last[cid] = (False, f"state {state}") if state != "running" else self._probe(cid)
                else:
                    done, last[cid] = state == "running", f"state {state}"
                if done:
                    pending.remove(cid)
                    ready.append(cid)
                    self._say(f"{cid} is ready")
            if not pending:
                return
            if self.clock.now() >= deadline:
                cid = pending[0]
                raise _Failure(cid, f"{cid} was not ready within {self.health_timeout:g} s ({last.get(cid)})")
            self.clock.sleep(POLL_INTERVAL)

    def _probe(self, cid: str) -> tuple[bool, str]:
        runtime = self.plan.runtimes[cid]
        port = self._port(cid)
        try:
            response = self.http.request("GET", port, runtime.health, None, PROBE_TIMEOUT)
        except (OSError, http.client.HTTPException) as error:
            return False, f"GET {runtime.health}: {error or type(error).__name__}"
        return 200 <= response.status < 300, f"GET {runtime.health} answered {response.status}"

    def _step(self, index: int, step: SmokeStep, captured: dict) -> dict:
        result = {"index": index, "name": step.name, "service": step.service, "method": step.method,
                  "path": step.path, "status": None, "ok": False, "detail": None}
        try:
            path, body = render_step(step, captured)
        except ValueError as error:
            return {**result, "detail": str(error)}
        result["path"] = path
        payload = json.dumps(body).encode() if step.has_body else None
        try:
            response = self.http.request(step.method, self._port(step.service), path, payload, STEP_TIMEOUT)
        except (OSError, http.client.HTTPException) as error:
            return {**result, "detail": f"{step.method} {path} got no response: {error or type(error).__name__}"}
        result["status"] = response.status
        shown = response.body[:SHOWN_BODY].decode(errors="replace")
        if response.status != step.expect_status:
            return {**result, "detail": f"expected status {step.expect_status}, got {response.status}; body: {shown}"}
        if step.has_expect_json or step.capture:
            try:
                document = json.loads(response.body)
            except ValueError:
                return {**result, "detail": f"the response is not JSON: {shown}"}
            if step.has_expect_json and not matches(step.expect_json, document):
                return {**result, "detail": f"the response {shown} does not match expect_json "
                                            f"{json.dumps(step.expect_json)}"}
            for variable, key in step.capture:
                value = document.get(key) if isinstance(document, dict) else None
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    return {**result, "detail": f"cannot capture {variable}: the response's top-level {key!r} is "
                                                f"not a string or an integer ({shown})"}
                captured[variable] = value
        return {**result, "ok": True}

    def _say(self, line: str) -> None:
        print(line, file=self.out or sys.stderr, flush=True)


def matches(expected, actual) -> bool:
    """smoke.json's expect_json rule: an object matches when each of its keys is in the
    response and matches there (extra keys allowed); anything else must be equal, and
    a boolean never equals a number."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and matches(value, actual[key])
                                                for key, value in expected.items())
    return _same(expected, actual)


def _same(expected, actual) -> bool:
    """JSON equality in which a boolean never equals a number."""
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and expected.keys() == actual.keys()
                and all(_same(value, actual[key]) for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(expected) == len(actual)
                and all(_same(e, a) for e, a in zip(expected, actual)))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, (int, float)) != isinstance(actual, (int, float)):
        return False
    return expected == actual


def workdir(workspace: Path) -> Path:
    """The git-ignored directory under ``workspace`` that holds each local run's compose file."""
    return keep_out_of_git(workspace, ".autocode-components").joinpath(*LOCAL_RUN_DIR)


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]
