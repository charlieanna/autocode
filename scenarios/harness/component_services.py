"""Actual delivered HTTP services, with independent bounded CLI ownership.

The routine transport substitutes Docker only, never application code. No tools imports.
"""
import contextlib
import http.client
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from . import processes


def run_command(command, timeout):
    """Stop captured birth identities before reaping the private session leader."""
    if processes.psutil is None:
        raise processes.SupervisionUnavailable("local oracle CLI supervision requires psutil")
    owned, errors, handlers = [], [], {}
    child = failure = None
    expired = cancelled = False
    with tempfile.TemporaryFile(mode="w+t", errors="replace") as output, \
            tempfile.TemporaryFile(mode="w+t", errors="replace") as stderr:
        try:
            child = subprocess.Popen(command, stdout=output, stderr=stderr, start_new_session=True)
            if threading.current_thread() is threading.main_thread():
                def interrupted(signum, frame):
                    nonlocal cancelled
                    if not cancelled:
                        cancelled = True
                        raise KeyboardInterrupt(f"oracle CLI interrupted by signal {signum}")
                for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                    handlers[signum] = signal.signal(signum, interrupted)
            parent = processes.psutil.Process(child.pid)
            owned.append(parent)
            deadline = time.monotonic() + timeout
            while True:
                for process in list(owned):
                    processes._capture(process, owned, errors)
                if parent.status() == processes.psutil.STATUS_ZOMBIE:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    expired = True
                    break
                time.sleep(min(.02, remaining))
        except BaseException as error:
            failure = error
        finally:
            cancelled = True
            try:
                if child is not None:
                    for process in list(owned):
                        processes._capture(process, owned, errors)
                    # Unreaped Popen ownership pins the group, including fast orphaned helpers.
                    for pid in processes.psutil.pids():
                        try:
                            if os.getpgid(pid) == child.pid:
                                process = processes.psutil.Process(pid)
                                if process.is_running() and os.getpgid(process.pid) == child.pid and process not in owned:
                                    owned.append(process)
                        except (ProcessLookupError, processes.psutil.NoSuchProcess):
                            pass
                        except (OSError, processes.psutil.Error) as error:
                            errors.append(f"cannot inspect owned CLI group candidate {pid}: {error}")
                    for process in list(owned):
                        processes._capture(process, owned, errors)
                    for process in reversed(owned):
                        processes._signal(process, "kill", errors)
                    if not any(process.pid == child.pid for process in owned):
                        child.kill()
                    _, alive = processes.psutil.wait_procs([p for p in owned if p.pid != child.pid], timeout=2)
                    for process in alive:
                        if processes._live(process):
                            errors.append(f"owned CLI helper {process.pid} remains alive after cancellation")
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        errors.append("owned CLI remains alive after cancellation")
            finally:
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
        output.seek(0)
        stderr.seek(0)
        out, err = output.read(), stderr.read()
    if errors:
        error = processes.SupervisionUnavailable("oracle CLI helper cleanup incomplete: " + "; ".join(errors))
        error.cleanup_errors = errors
        raise error from failure
    if failure is not None:
        raise failure
    if expired:
        raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(command, child.returncode, out, err)


@contextlib.contextmanager
def running(project, architecture):
    if processes.psutil is None:
        raise processes.SupervisionUnavailable("sample HTTP supervision requires psutil")
    rows = json.loads((architecture / "components.json").read_text())
    pending = {row["id"]: row["runtime"] for row in rows}
    children, ports = [], {}
    with contextlib.ExitStack() as stack:
        try:
            while pending:
                layer = [cid for cid, rt in pending.items() if set(rt.get("runtime_depends_on", [])) <= ports.keys()]
                if not layer:
                    raise ValueError("runtime graph has no start order")
                for cid in layer:
                    rt = pending.pop(cid)
                    if rt.get("kind") != "service" or rt.get("start") != "python3 server.py":
                        raise ValueError("the sample transport supports stdlib Python HTTP services only")
                    env = {"PATH": os.defpath, "PORT": "0", "PYTHONDONTWRITEBYTECODE": "1", **rt.get("env", {}),
                           "BIND_HOST": "127.0.0.1"}
                    for dependency in rt.get("runtime_depends_on", []):
                        env[dependency.upper().replace("-", "_") + "_URL"] = f"http://127.0.0.1:{ports[dependency]}"
                    log = stack.enter_context(tempfile.TemporaryFile())
                    child = subprocess.Popen([sys.executable, "-u", str(project / "components" / cid / "server.py")],
                                             cwd=project / "components" / cid, env=env, stdout=subprocess.PIPE,
                                             stderr=log, start_new_session=True)
                    children.append((child, processes.psutil.Process(child.pid)))
                    if not select.select([child.stdout], [], [], 5)[0]:
                        raise RuntimeError(f"{cid} did not announce readiness within 5 s")
                    line = child.stdout.readline().decode().strip().split()
                    if len(line) != 2 or line[0] != "ready" or not line[1].isdigit():
                        log.seek(0)
                        raise RuntimeError(f"{cid} did not announce a port: {log.read().decode()[-1000:]}")
                    ports[cid] = int(line[1])
            yield ports
        finally:
            errors = []
            for child, identity in children:
                owned = [identity]
                processes._capture(identity, owned, errors)
                for process in reversed(owned):
                    processes._signal(process, "kill", errors)
                _, survivors = processes.psutil.wait_procs([p for p in owned if p.pid != child.pid], timeout=2)
                for process in survivors:
                    if processes._live(process):
                        errors.append(f"owned HTTP helper {process.pid} remains alive")
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    errors.append(f"owned HTTP service {child.pid} remains alive")
                child.stdout.close()
            if errors:
                raise RuntimeError("sample service cleanup incomplete: " + "; ".join(errors))


def request(port, method, path, document=None, *, json_required=False):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = json.dumps(document).encode() if document is not None else None
        connection.request(method, path, payload, {"Content-Type": "application/json"} if payload else {})
        response = connection.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), json.loads(body) if json_required else body or None
    finally:
        connection.close()


@contextlib.contextmanager
def running_compose(compose_file, receipt):
    """Opt-in independent real-engine execution and labeled-resource cleanup."""
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("real Docker was requested but docker is not installed")
    context = os.environ.get("DOCKER_CONTEXT")
    endpoint = "" if context else os.environ.get("DOCKER_HOST", "")
    if not endpoint:
        result = run_command([docker, "context", "inspect", *([context] if context else []),
                              "--format", "{{.Endpoints.docker.Host}}"], 20)
        if result.returncode:
            raise RuntimeError("cannot inspect local Docker context")
        endpoint = result.stdout.strip()
    if not endpoint.startswith(("unix://", "npipe://")):
        raise RuntimeError("the real-engine oracle refuses a remote Docker context")
    # An explicit host pins every operation even if the user's context changes.
    docker_command = [docker, "--host", endpoint]
    project = "autocode-oracle-" + uuid.uuid4().hex[:12]
    command = [*docker_command, "compose", "-p", project, "-f", str(compose_file)]
    receipt.update(project=project, compose_file=str(compose_file), torn_down=False)

    def call(*args, timeout=180):
        try:
            result = run_command([*command, *args], timeout)
        except BaseException as error:
            if getattr(error, "cleanup_errors", None):
                receipt.setdefault("helper_cleanup_errors", []).extend(error.cleanup_errors)
            raise
        if result.returncode:
            raise RuntimeError(f"oracle compose {' '.join(args)} failed: {(result.stderr or result.stdout)[-1000:]}")
        return result.stdout

    try:
        call("up", "-d", "--build")
        services = json.loads(compose_file.read_text())["services"]
        ports = {cid: int(call("port", cid, service["ports"][0].rsplit(":", 1)[1]).strip().rsplit(":", 1)[1])
                 for cid, service in services.items()}
        deadline = time.monotonic() + 30
        pending = set(ports)
        while pending:
            for cid in list(pending):
                try:
                    if 200 <= request(ports[cid], "GET", "/health")[0] < 300:
                        pending.remove(cid)
                except (OSError, http.client.HTTPException):
                    pass
            if time.monotonic() >= deadline:
                raise RuntimeError(f"oracle health timeout: {sorted(pending)}")
            if pending:
                time.sleep(.05)
        yield ports
    finally:
        try:
            receipt["logs"] = call("logs", "--no-color", "--tail", "20", timeout=30)
        except Exception as error:
            receipt["logs"] = str(error)
        call("down", "-v", "--remove-orphans", "--rmi", "local", timeout=120)
        if receipt.get("helper_cleanup_errors"):
            raise RuntimeError("oracle CLI helper cleanup incomplete: " + "; ".join(receipt["helper_cleanup_errors"]))
        for kind, args in (("containers", ["ps", "-aq"]), ("networks", ["network", "ls", "-q"]),
                           ("images", ["image", "ls", "-q"])):
            result = run_command([*docker_command, *args, "--filter", f"label=com.docker.compose.project={project}"], 20)
            receipt[kind] = result.stdout.strip()
            if result.returncode or receipt[kind]:
                raise RuntimeError(f"oracle cleanup left {kind}: {receipt[kind]}")
        receipt["torn_down"] = True
