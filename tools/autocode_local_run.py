"""Run an integrated architecture's components together on this machine and smoke-check them.

`autocode components ARCH --integrate TARGET --run-local` (docs/task-lanes.md,
"Running the combined system locally") calls this after integrating. In order:

1. ``prepare``, before any component is built: every component declares a
   runtime block, at least one is a service, the runtime graph has a start
   order, and ARCHITECTURE/smoke.json is valid and talks only to services.
   ``check_docker`` then confirms Docker Compose 2.17 or newer and a reachable
   daemon on this machine.
2. ``LocalRun.run``, after integration: checks the combined tree holds each
   running component's directory (and Dockerfile), writes the Compose file
   (autocode_compose_file) into an AutoCode-owned directory, and starts the
   components one start_layers layer at a time with ``up --no-deps``, waiting
   for each layer to be ready before the next: a service when GET <health> on
   its loopback-published port answers 2xx, a component with a health command
   when Compose reports it "healthy", any other once it is running. Then it
   sends each smoke step to 127.0.0.1:<published port>, in order, stopping at
   the first that fails, and always tears the project down and removes the
   images it built (``down -v --remove-orphans --rmi local``), on failure and
   on KeyboardInterrupt too, unless asked to keep it running.

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
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    from .autocode_component_runtime import (
        ComponentRuntime,
        SmokeCheck,
        SmokeStep,
        check_smoke,
        load_smoke,
        render_step,
        require_runnable,
        start_layers,
    )
    from .autocode_compose_file import compose_document, render
    from .autocode_workspaces import keep_out_of_git
except ImportError:
    from autocode_component_runtime import (
        ComponentRuntime,
        SmokeCheck,
        SmokeStep,
        check_smoke,
        load_smoke,
        render_step,
        require_runnable,
        start_layers,
    )
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
# It starts with a dot so it can never be a component's worktree, .autocode-components/<id>:
# a component id starts with a letter or digit (autocode_multicomponent.SAFE_NAME).
LOCAL_RUN_DIR = (".local-run",)
# The oldest Compose that reads the generated file: dockerfile_inline came in 2.17.
MIN_COMPOSE = (2, 17)
# Probes and smoke requests go to 127.0.0.1, so the daemon must publish ports on this machine.
LOCAL_ENDPOINTS = ("unix://", "npipe://")
# Container states in which a container will not become ready.
STOPPED = ("exited", "dead", "missing", "removing")


class DockerUnavailable(RuntimeError):
    """docker, Compose v2 or the Docker daemon cannot be used."""


class DockerCleanupUnavailable(DockerUnavailable):
    """An owned CLI helper could not be proven stopped."""


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
        from . import autocode_captured_process as captured
    except ImportError:
        import autocode_captured_process as captured
    try:
        proc = captured.run(argv, timeout=timeout)
    except FileNotFoundError:
        raise DockerUnavailable(
            f"{argv[0]} is not installed or not on PATH; --run-local needs Docker with Compose v2 (docker compose)"
        ) from None
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout.decode(errors="replace") if isinstance(expired.stdout, bytes) else expired.stdout or ""
        err = expired.stderr.decode(errors="replace") if isinstance(expired.stderr, bytes) else expired.stderr or ""
        return CommandResult(124, out, err + f"\n{' '.join(argv[:3])} ... did not finish within {timeout:g} s")
    except captured.ProcessError as error:
        raise DockerCleanupUnavailable(f"cannot verify owned local CLI helper cleanup: {error}") from error
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
    endpoint: str | None = None


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


def docker_checks(runner: Runner = subprocess_runner, env: Mapping[str, str] | None = None) -> list[dict]:
    """Read-only readiness checks; reject remote endpoints before daemon contact."""
    env = os.environ if env is None else env
    checks = []

    def probe(name, argv):
        try:
            return runner(argv, COMMAND_TIMEOUT)
        except (OSError, DockerUnavailable) as error:
            checks.append({"name": name, "ok": False, "detail": str(error)})
            return None

    compose = probe("docker:compose", ["docker", "compose", "version", "--short"])
    if compose is None:
        return checks
    detail = ""
    if compose.returncode != 0:
        detail = (
            f"`docker compose version` failed; --run-local needs Compose v2 (the docker "
            f"compose plugin): {_tail(compose.stderr or compose.stdout)}"
        )
    found = re.match(r"v?(\d+)\.(\d+)", compose.stdout.strip())
    if not detail and (not found or (int(found.group(1)), int(found.group(2))) < MIN_COMPOSE):
        detail = (
            f"--run-local needs Docker Compose {'.'.join(map(str, MIN_COMPOSE))} or newer "
            f"(found {_tail(compose.stdout, 80) or 'no version'}); update Docker Compose"
        )
    checks.append(
        {"name": "docker:compose", "ok": not detail, "detail": detail or f"Docker Compose {_tail(compose.stdout, 80)}"}
    )
    # The Docker CLI gives DOCKER_CONTEXT precedence over DOCKER_HOST.
    endpoint = "" if env.get("DOCKER_CONTEXT") else env.get("DOCKER_HOST", "")
    if not endpoint:
        context = probe(
            "docker:context",
            [
                "docker",
                "context",
                "inspect",
                *([env["DOCKER_CONTEXT"]] if env.get("DOCKER_CONTEXT") else []),
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
        )
        if context is not None:
            if context.returncode != 0:
                checks.append(
                    {
                        "name": "docker:context",
                        "ok": False,
                        "detail": "cannot tell where the Docker daemon runs (`docker context inspect` failed): "
                        + _tail(context.stderr or context.stdout),
                    }
                )
            else:
                endpoint = context.stdout.strip()
    if endpoint:
        local = endpoint.startswith(LOCAL_ENDPOINTS)
        checks.append(
            {
                "name": "docker:context",
                "ok": local,
                "endpoint": endpoint,
                "detail": f"local Docker endpoint {_tail(endpoint, 200)}"
                if local
                else "--run-local needs a Docker daemon on this machine, reached through a local "
                "unix:// or npipe:// socket, because it checks the published ports on 127.0.0.1; "
                f"the daemon is at {_tail(endpoint, 200)!r} (DOCKER_HOST or the current docker context)",
            }
        )
    elif not any(check["name"] == "docker:context" for check in checks):
        checks.append({"name": "docker:context", "ok": False, "detail": "Docker context has no daemon endpoint"})
    if not any(check["name"] == "docker:context" and not check["ok"] for check in checks):
        server = probe("docker:daemon", ["docker", "--host", endpoint, "version", "--format", "{{.Server.Version}}"])
        if server is not None:
            ok = server.returncode == 0 and bool(server.stdout.strip())
            checks.append(
                {
                    "name": "docker:daemon",
                    "ok": ok,
                    "detail": f"Docker daemon {_tail(server.stdout, 80)}"
                    if ok
                    else "the Docker daemon is not reachable (`docker version` failed); start Docker and "
                    f"try again: {_tail(server.stderr or server.stdout)}",
                }
            )
    return checks


def check_docker(runner: Runner = subprocess_runner, env: Mapping[str, str] | None = None) -> str:
    """Validate readiness and return the local endpoint to pin subsequent commands."""
    checks = docker_checks(runner, env)
    for check in checks:
        if not check["ok"]:
            raise DockerUnavailable(check["detail"])
    return next(check["endpoint"] for check in checks if check["name"] == "docker:context")


def check_tree(plan: LocalRunPlan, tree: Path) -> None:
    """The combined tree must hold what each running component is built from. Raises ValueError."""
    for cid, runtime in sorted(plan.runtimes.items()):
        if runtime is None or not runtime.runs:
            continue
        directory = tree / "components" / cid
        if not directory.is_dir():
            raise ValueError(f"{cid} cannot be started: {directory} does not exist in the integrated tree")
        if runtime.dockerfile is not None and not (directory / runtime.dockerfile).is_file():
            raise ValueError(
                f"{cid} cannot be started: its dockerfile {directory / runtime.dockerfile} does not "
                f"exist in the integrated tree"
            )


class _Failure(Exception):
    """``component`` is the one that failed, when one did; ``involved`` the components
    whose logs explain it (by default just that one)."""

    def __init__(self, component: str | None, detail: str, step: str | None = None, involved=None):
        super().__init__(detail)
        self.component, self.detail, self.step = component, detail, step
        self.involved = list(involved) if involved is not None else [component] if component else []


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

    # --rmi local: every run is a new project, so its built images would otherwise pile up.
    DOWN = ("down", "-v", "--remove-orphans", "--rmi", "local")

    def compose(self, *args: str) -> list:
        return [
            "docker",
            *(["--host", self.plan.endpoint] if self.plan.endpoint else []),
            "compose",
            "-p",
            self.project,
            "-f",
            str(self.compose_file),
            *args,
        ]

    def run(self) -> dict:
        """Start, check and tear down. Returns the summary's local_run entry; its status
        is "passed" or "failed". A KeyboardInterrupt still tears down, then propagates."""
        summary: dict[str, Any] = {
            "status": "failed",
            "project": self.project,
            "compose_file": str(self.compose_file),
            "layers": self.plan.layers,
            "ready": [],
            "ports": self.ports,
            "steps": [],
            "failed_component": None,
            "failed_step": None,
            "detail": None,
            "logs": None,
            "torn_down": False,
            "stop_command": None,
            "cleanup_detail": None,
            "kept_running": False,
        }
        started = False
        self._helper_cleanup_failure = None
        handlers = {}
        if threading.current_thread() is threading.main_thread():

            def interrupted(signum, frame):
                raise SystemExit(128 + signum)

            for signum in (signal.SIGTERM, signal.SIGHUP):
                handlers[signum] = signal.signal(signum, interrupted)
        try:
            check_tree(self.plan, self.tree)
            if self.plan.endpoint is None and self.runner is subprocess_runner:
                self.plan = replace(self.plan, endpoint=check_docker(self.runner))
            if self.plan.endpoint is not None and not self.plan.endpoint.startswith(LOCAL_ENDPOINTS):
                raise DockerUnavailable("local run endpoint must be a local unix:// or npipe:// socket")
            summary["docker_endpoint"] = self.plan.endpoint
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
                    raise _Failure(
                        step.service, f"smoke step {index} ({step.name}) failed: {result['detail']}", step=step.name
                    )
            summary["status"] = "passed"
        except BaseException as failure:
            summary["status"] = "failed"
            component, involved = getattr(failure, "component", None), getattr(failure, "involved", [])
            summary.update(
                failed_component=component,
                failed_step=getattr(failure, "step", None),
                detail=getattr(failure, "detail", str(failure) or type(failure).__name__),
            )
            self._say(f"--run-local failed: {summary['detail']}")
            if involved and started:
                summary["logs"] = self._logs(involved)
                self._say(f"last {self.log_lines} log lines of {', '.join(involved)}:\n{summary['logs']}")
            elif started:
                summary["logs"] = self._logs([cid for layer in self.plan.layers for cid in layer])
            if not isinstance(failure, Exception):
                raise
        finally:
            try:
                if started:
                    if self.keep_running:
                        summary["kept_running"] = True
                        summary["stop_command"] = shlex.join(self.compose(*self.DOWN))
                        self._say(f"left running; stop it with: {summary['stop_command']}")
                    else:
                        down_detail = self._down()
                        summary["cleanup_detail"] = (
                            "; ".join(filter(None, [self._helper_cleanup_failure, down_detail])) or None
                        )
                        summary["torn_down"] = summary["cleanup_detail"] is None
                        if not summary["torn_down"]:
                            summary["status"] = "failed"
                            summary["detail"] = "; ".join(filter(None, [summary["detail"], summary["cleanup_detail"]]))
                            summary["stop_command"] = shlex.join(self.compose(*self.DOWN))
                            if summary["logs"] is None:
                                summary["logs"] = self._logs([cid for layer in self.plan.layers for cid in layer])
                            self._say(
                                f"--run-local failed: {summary['detail']}\n"
                                f"cleanup incomplete; recover with: {summary['stop_command']}"
                            )
            finally:
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
        return summary

    def _write_compose_file(self) -> None:
        self.compose_file.parent.mkdir(parents=True, exist_ok=True)
        self.compose_file.write_text(render(compose_document(self.plan.runtimes, self.tree)), encoding="utf-8")

    def _up(self, layer: list) -> None:
        result = self._run_command(self.compose("up", "-d", "--build", "--no-deps", *layer), UP_TIMEOUT)
        if result.returncode != 0:
            raise _Failure(
                layer[0] if len(layer) == 1 else None,
                f"docker compose up failed for {', '.join(layer)}: {_tail(result.stderr or result.stdout)}",
                involved=layer,
            )

    def _run_command(self, argv, timeout):
        try:
            return self.runner(argv, timeout)
        except DockerCleanupUnavailable as error:
            self._helper_cleanup_failure = str(error)
            raise

    def _down(self) -> str | None:
        try:
            result = self._run_command(self.compose(*self.DOWN), COMMAND_TIMEOUT)
        except Exception as error:
            return f"could not tear down {self.project}: {_tail(str(error)) or type(error).__name__}"
        if result.returncode != 0:
            return f"could not tear down {self.project} (exit {result.returncode}): {_tail(result.stderr or result.stdout)}"
        return None

    def _logs(self, components: list) -> str:
        try:
            result = self._run_command(
                self.compose("logs", "--no-color", "--tail", str(self.log_lines), *components), COMMAND_TIMEOUT
            )
        except Exception as error:
            return str(error)
        return (result.stdout + result.stderr).rstrip("\n")

    def _states(self, involved: list) -> dict:
        """Each container's (State, Health) by service, from `ps --all --format json`, which
        prints one JSON array (older Compose v2) or one object per line (newer). A failure
        names ``involved``, the components being waited on, for their logs."""
        result = self._run_command(self.compose("ps", "--all", "--format", "json"), COMMAND_TIMEOUT)
        if result.returncode != 0:
            raise _Failure(
                None, f"docker compose ps failed: {_tail(result.stderr or result.stdout)}", involved=involved
            )
        text = result.stdout.strip()
        try:
            rows = (
                json.loads(text)
                if text.startswith("[")
                else [json.loads(line) for line in text.splitlines() if line.strip()]
            )
        except ValueError:
            raise _Failure(
                None, f"docker compose ps printed something that is not JSON: {_tail(text)}", involved=involved
            ) from None
        return {
            row.get("Service"): (str(row.get("State", "")).lower(), str(row.get("Health", "")).lower())
            for row in rows
            if isinstance(row, dict)
        }

    def _port(self, cid: str) -> int:
        if cid not in self.ports:
            runtime = self.plan.runtimes[cid]
            result = self._run_command(self.compose("port", cid, str(runtime.port)), COMMAND_TIMEOUT)
            address = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
            host, _, port = address.rpartition(":")
            if result.returncode != 0 or not port.isdigit():
                # `port` fails for a container that has stopped: say so, as the wait does.
                state, _ = self._states([cid]).get(cid, ("missing", ""))
                if state in STOPPED:
                    raise _Failure(cid, f"{cid} is not running (state {state})")
                raise _Failure(
                    cid,
                    f"cannot find the host port published for {cid}'s port {runtime.port}: "
                    f"{_tail(result.stderr or address)}",
                )
            self.ports[cid] = int(port)
        return self.ports[cid]

    def _wait_ready(self, layer: list, ready: list) -> None:
        deadline = self.clock.now() + self.health_timeout
        pending = list(layer)
        last: dict[str, str] = {}
        while True:
            states = self._states(pending)
            for cid in list(pending):
                state, health = states.get(cid, ("missing", ""))
                if state in STOPPED:
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
        result = {
            "index": index,
            "name": step.name,
            "service": step.service,
            "method": step.method,
            "path": step.path,
            "status": None,
            "ok": False,
            "detail": None,
        }
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
                return {
                    **result,
                    "detail": f"the response {shown} does not match expect_json {json.dumps(step.expect_json)}",
                }
            for variable, key in step.capture:
                value = document.get(key) if isinstance(document, dict) else None
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    return {
                        **result,
                        "detail": f"cannot capture {variable}: the response's top-level {key!r} is "
                        f"not a string or an integer ({shown})",
                    }
                captured[variable] = value
        return {**result, "ok": True}

    def _say(self, line: str) -> None:
        print(line, file=self.out or sys.stderr, flush=True)


def matches(expected, actual) -> bool:
    """smoke.json's expect_json rule: an object matches when each of its keys is in the
    response and matches there (extra keys allowed); anything else must be equal, and
    a boolean never equals a number."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(value, actual[key]) for key, value in expected.items()
        )
    return _same(expected, actual)


def _same(expected, actual) -> bool:
    """JSON equality in which a boolean never equals a number."""
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and expected.keys() == actual.keys()
            and all(_same(value, actual[key]) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_same(e, a) for e, a in zip(expected, actual, strict=False))
        )
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, (int, float)) != isinstance(actual, (int, float)):
        return False
    return expected == actual


def workdir(workspace: Path) -> Path:
    """The git-ignored directory under ``workspace`` that holds each local run's compose file.

    Resolved so the emitted compose path is canonical (macOS ``/var`` vs
    ``/private/var``) regardless of how the caller spelled the workspace.
    """
    return keep_out_of_git(Path(workspace).resolve(), ".autocode-components").joinpath(*LOCAL_RUN_DIR)


def _tail(text: str, limit: int = 800) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]
