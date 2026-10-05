"""How each component of an architecture runs once the system is combined.

A row of ARCHITECTURE/components.json may carry an optional "runtime" block
(docs/task-lanes.md, "Declaring how a component runs"). It is a build input:
it adds sentences to that component's Builder brief, and because it lives
inside components.json it is part of the saved build's identity
(autocode_multicomponent.Architecture.fingerprint). Records without a block
behave exactly as before.

Four kinds:
- service: a long-lived HTTP server. It needs a container port and a health
  path that answers 2xx once it is ready.
- worker: a long-running process with no port. Health is optional; when
  given, it is a command run inside its container.
- database: built from its own Dockerfile (a start command on the Python base
  image cannot run a database). Its port is required: it is never published,
  but the database's Builder and each dependent's Builder are both told it,
  since neither sees the other's code. Health is a command run inside its
  container.
- library: never started on its own; it carries nothing but its kind.

The block is data a model may have written, so it is validated strictly. No
string may contain a backtick: a backticked span in a brief becomes a literal
the goal contract must keep (autocode_brief_literals), and these sentences are
obligations to trace, not literals. Pure: runs nothing and reads no file.
Imports nothing from AutoCode, so autocode_multicomponent can import it
without joining an import cycle.
"""
from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# The image a "start" command runs in. Fixed on purpose: anything else ships a Dockerfile.
START_IMAGE = "python:3.12-slim"
KINDS = ("service", "worker", "database", "library")
# Keys each kind may carry besides "kind".
KIND_KEYS = {
    "service": frozenset({"port", "start", "dockerfile", "health", "runtime_depends_on", "env"}),
    "worker": frozenset({"start", "dockerfile", "health", "runtime_depends_on", "env"}),
    "database": frozenset({"port", "dockerfile", "health", "runtime_depends_on", "env"}),
    "library": frozenset(),
}
KEYS = frozenset({"kind"}).union(*KIND_KEYS.values())
# A component that runs becomes a container's service name and the host name other
# containers use, and its id is the stem of environment variable names: a DNS label
# that starts with a letter. Library ids keep the ordinary component-id rule.
SERVICE_ID = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
# Names every container's /etc/hosts maps to the container itself (Docker writes them,
# and /etc/hosts is read before DNS), so a component with one of these ids could never
# be reached: a dependent connecting to it would reach itself.
CONTAINER_LOCAL_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback", "ip6-localnet",
                                   "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters"})
# What str.splitlines breaks a line on, besides NUL: none may appear in a string that a
# brief states as one line.
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
HEALTH_PATH = re.compile(r"/[A-Za-z0-9._~/-]{0,127}")
DOCKERFILE_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
MAX_ENV = 32
MAX_TEXT = 1000
MAX_DOCKERFILE = 128
MAX_COMMAND = 32


@dataclass(frozen=True)
class ComponentRuntime:
    kind: str
    port: int | None = None
    start: str | None = None
    dockerfile: str | None = None
    health: str | None = None  # service: the HTTP path that answers 2xx once ready
    health_command: tuple[str, ...] = ()  # worker or database: argv run inside its container
    runtime_depends_on: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()

    @property
    def runs(self) -> bool:
        return self.kind != "library"

    @property
    def is_service(self) -> bool:
        return self.kind == "service"

    @classmethod
    def load(cls, row: dict, component_id: str) -> ComponentRuntime | None:
        """The row's runtime block, or None when it has none. Raises ValueError."""
        if "runtime" not in row:
            return None
        block = row["runtime"]
        if not isinstance(block, dict):
            raise ValueError("runtime must be a JSON object such as {\"kind\": \"library\"}")
        for key in sorted(block):
            if key not in KEYS:
                close = difflib.get_close_matches(key, sorted(KEYS), n=1, cutoff=0.6)
                raise ValueError(f"unknown key {key!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
        kind = block.get("kind")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)} (found {kind!r})")
        extra = sorted(set(block) - KIND_KEYS[kind] - {"kind"})
        if extra:
            raise ValueError(_not_for_kind(kind, extra[0]))
        if kind == "library":
            return cls(kind=kind)
        if not SERVICE_ID.fullmatch(component_id):
            raise ValueError(f"a {kind}'s component id must be a lowercase DNS label that starts with a letter "
                             f"(a-z, digits and '-', at most 63 characters, not ending in '-'), because it "
                             f"becomes its container's host name and the stem of environment variable names "
                             f"(found {component_id!r})")
        if component_id in CONTAINER_LOCAL_HOSTS:
            raise ValueError(f"a {kind} cannot have the id {component_id!r}: inside every container that name "
                             f"means the container itself, so a component connecting to it would reach itself; "
                             f"choose another id")
        port = _port(block, kind)
        start, dockerfile = _build(block, kind, component_id)
        health, health_command = _health(block, kind)
        depends_on = _depends_on(block)
        env = _env(block, depends_on)
        return cls(kind=kind, port=port, start=start, dockerfile=dockerfile, health=health,
                   health_command=health_command, runtime_depends_on=depends_on, env=env)


def _not_for_kind(kind: str, key: str) -> str:
    if kind == "library":
        return f"a library is never started, so its runtime block carries only kind, not {key!r}"
    if kind == "worker" and key == "port":
        return "a worker has no port; a component that answers HTTP requests is a service"
    if kind == "database" and key == "start":
        return (f"a database needs a dockerfile, not start: a start command runs in {START_IMAGE}, "
                f"which cannot run a database")
    return f"a {kind} does not take {key!r}"


def _text(key: str, value, *, limit: int = MAX_TEXT, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string (found {value!r})")
    if not value and not empty:
        raise ValueError(f"{key} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{key} must be at most {limit} characters")
    if any(char in value for char in "\0" + LINE_BREAKS):
        raise ValueError(f"{key} must be a single line without NUL or line-break characters (CR, LF, VT, FF, "
                         f"NEL, U+2028, U+2029 and the like)")
    if "`" in value:
        raise ValueError(f"{key} must not contain a backtick (`)")
    return value


def _port(block: dict, kind: str) -> int | None:
    if "port" not in block:
        if kind == "service":
            raise ValueError("a service needs port, the container port it listens on (1-65535)")
        if kind == "database":
            raise ValueError("a database needs port, the container port it accepts connections on (1-65535): it "
                             "is never published, but the components that connect to it are told this port")
        return None
    port = block["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"port must be an integer from 1 to 65535 (found {port!r})")
    return port


def _build(block: dict, kind: str, component_id: str) -> tuple[str | None, str | None]:
    """(start, dockerfile): exactly one, and a database always has a dockerfile."""
    has_start, has_dockerfile = "start" in block, "dockerfile" in block
    if kind == "database" and not has_dockerfile:
        raise ValueError(f"a database needs dockerfile, the Dockerfile inside components/{component_id}/ that "
                         f"builds and starts it")
    if has_start == has_dockerfile:
        raise ValueError(f"a {kind} needs exactly one of start (a shell command run in {START_IMAGE}) or "
                         f"dockerfile (a Dockerfile inside components/{component_id}/)")
    if has_start:
        return _text("start", block["start"]), None
    dockerfile = block["dockerfile"]
    if not isinstance(dockerfile, str) or not dockerfile:
        raise ValueError(f"dockerfile must be a nonempty string (found {dockerfile!r})")
    if len(dockerfile) > MAX_DOCKERFILE:
        raise ValueError(f"dockerfile must be at most {MAX_DOCKERFILE} characters")
    if dockerfile[0] in "/\\`":
        raise ValueError(f"dockerfile must be a relative path inside components/{component_id}/ (found {dockerfile!r})")
    if any(segment in (".", "..") or not DOCKERFILE_SEGMENT.fullmatch(segment) for segment in dockerfile.split("/")):
        raise ValueError(f"dockerfile must be a relative path inside components/{component_id}/ made of '/'-separated "
                         f"segments of letters, digits, '.', '_' and '-', with no empty, '.' or '..' segment "
                         f"(found {dockerfile!r})")
    return None, dockerfile


def _health(block: dict, kind: str) -> tuple[str | None, tuple[str, ...]]:
    """(health path, health command): a service's is an HTTP path; a worker's or a
    database's is a command run inside its container, optional for a worker."""
    if "health" not in block:
        if kind == "service":
            raise ValueError("a service needs health, the HTTP path that answers 2xx once it is ready, "
                             "such as \"/health\"")
        if kind == "database":
            raise ValueError("a database needs health, a command run inside its container that exits 0 once it "
                             "accepts connections, as a JSON array of strings such as [\"pg_isready\"]")
        return None, ()
    health = block["health"]
    if kind == "service":
        if not isinstance(health, str) or not health.startswith("/"):
            raise ValueError(f"a service's health must be a URL path starting with '/' (found {health!r})")
        if ".." in health:
            raise ValueError(f"health must not contain '..' (found {health!r})")
        if not HEALTH_PATH.fullmatch(health):
            raise ValueError(f"health may contain only letters, digits and '.', '_', '~', '/', '-', at most 128 "
                             f"characters in all (found {health!r})")
        return health, ()
    if not isinstance(health, list) or not 1 <= len(health) <= MAX_COMMAND:
        raise ValueError(f"a {kind}'s health must be a command run inside its container, a JSON array of 1 to "
                         f"{MAX_COMMAND} strings such as [\"python3\", \"check.py\"] (found {health!r})")
    return None, tuple(_text(f"health[{index}]", argument) for index, argument in enumerate(health))


def _depends_on(block: dict) -> tuple[str, ...]:
    depends_on = block.get("runtime_depends_on", [])
    if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
        raise ValueError(f"runtime_depends_on must be a list of component ids (found {depends_on!r})")
    for item in depends_on:
        if depends_on.count(item) > 1:
            raise ValueError(f"runtime_depends_on lists {item!r} more than once")
    return tuple(depends_on)


def _env(block: dict, depends_on: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    env = block.get("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"env must be a JSON object of NAME: value strings (found {env!r})")
    if len(env) > MAX_ENV:
        raise ValueError(f"env has {len(env)} entries; at most {MAX_ENV} are allowed")
    generated = {_variable(dep, suffix): dep for dep in depends_on for suffix in ("URL", "HOST", "PORT")}
    for name, value in env.items():
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f"env name {name!r} must be upper case: letters A-Z, digits and '_', not starting "
                             f"with a digit, at most 64 characters")
        if name == "PORT":
            raise ValueError("env name PORT is reserved: it carries this component's port")
        if name in generated:
            raise ValueError(f"env name {name} is reserved: it carries the address of the runtime dependency "
                             f"{generated[name]}")
        _text(f"env {name}", value, empty=True)
    return tuple(env.items())


def near_miss_runtime_key(row: dict) -> str | None:
    """A row key that is almost certainly a misspelled "runtime" (runtme, run_time,
    runtimes), which would otherwise be ignored like any other unknown row key."""
    for key in row:
        if key != "runtime" and difflib.get_close_matches(str(key), ["runtime"], n=1, cutoff=0.8):
            return key
    return None


def start_layers(runtimes: Mapping[str, ComponentRuntime | None]) -> list[list[str]]:
    """The components that run, in start order: each layer depends only on earlier
    layers, ids sorted within a layer. ``runtimes`` maps every component id to its
    block (None when it has none). Raises ValueError for a runtime dependency that
    is unknown, not started, a worker, the component itself, or part of a cycle."""
    running = {cid: runtime for cid, runtime in runtimes.items() if runtime is not None and runtime.runs}
    for cid, runtime in sorted(running.items()):
        for dep in runtime.runtime_depends_on:
            target = runtimes.get(dep)
            if dep == cid:
                raise ValueError(f"{cid} lists itself in runtime_depends_on")
            if dep not in runtimes:
                raise ValueError(f"{cid} runtime_depends_on unknown component {dep!r}")
            if target is None:
                raise ValueError(f"{cid} runtime_depends_on {dep}, which declares no runtime block")
            if target.kind == "library":
                raise ValueError(f"{cid} runtime_depends_on {dep}, a library, which is never started")
            if target.kind == "worker":
                raise ValueError(f"{cid} runtime_depends_on {dep}, a worker, which has no address to connect to; "
                                 f"only a service or a database can be a runtime dependency")
    layers: list[list[str]] = []
    done: set[str] = set()
    remaining = dict(running)
    while remaining:
        ready = sorted(cid for cid, runtime in remaining.items() if set(runtime.runtime_depends_on) <= done)
        if not ready:
            raise ValueError(f"runtime dependency cycle among {sorted(remaining)}")
        layers.append(ready)
        done.update(ready)
        for cid in ready:
            del remaining[cid]
    return layers


def _variable(component_id: str, suffix: str) -> str:
    return f"{component_id.upper().replace('-', '_')}_{suffix}"


def url_variable(component_id: str) -> str:
    """The variable holding a service dependency's base URL: link-api -> LINK_API_URL."""
    return _variable(component_id, "URL")


def dependency_variables(dependency_id: str, runtime: ComponentRuntime) -> dict[str, str]:
    """What a component is told about one runtime dependency: a service's base URL,
    or a database's host name and port."""
    if runtime.kind == "service":
        return {url_variable(dependency_id): f"http://{dependency_id}:{runtime.port}"}
    return {_variable(dependency_id, "HOST"): dependency_id, _variable(dependency_id, "PORT"): str(runtime.port)}


def service_environment(component_id: str, runtimes: Mapping[str, ComponentRuntime | None]) -> dict[str, str]:
    """The container environment of one running component, in order: PORT (when it has
    a port), each runtime dependency's address, then its declared env. ``runtimes``
    must already have passed start_layers."""
    runtime = runtimes[component_id]
    environment = {"PORT": str(runtime.port)} if runtime.port is not None else {}
    for dep in runtime.runtime_depends_on:
        environment.update(dependency_variables(dep, runtimes[dep]))
    environment.update(runtime.env)
    return environment


def brief_lines(component_id: str, runtimes: Mapping[str, ComponentRuntime | None]) -> list[str]:
    """The sentences a component's Builder brief gains from its runtime block; none
    without one. They carry requirement cue words on purpose (the Requirements stage
    must trace them) and no backticks (they add no literal the contract must keep)."""
    runtime = runtimes.get(component_id)
    if runtime is None:
        return []
    if not runtime.runs:
        return ["When the combined system runs, this component is not started on its own."]
    if runtime.kind == "service":
        lines = [f"When the combined system runs, this component runs as a long-lived HTTP service in its own "
                 f"container: it must listen on 0.0.0.0, not only on 127.0.0.1 or localhost, at the port in the "
                 f"PORT environment variable (which will be {runtime.port}), and answer GET {runtime.health} with "
                 f"a 2xx status once it is ready."]
    elif runtime.kind == "worker":
        lines = ["When the combined system runs, this component runs as a long-running worker process in its own "
                 "container, with no HTTP port and no published port: nothing connects to it, and it must keep "
                 "running until it is stopped rather than exit when it is idle or its work is done."]
    else:
        lines = [f"When the combined system runs, this component runs as a database in its own container: other "
                 f"components reach it only over the combined system's internal network, never through a "
                 f"published port, and it must accept connections on port {runtime.port} (also given in the PORT "
                 f"environment variable), listening on 0.0.0.0, not only on 127.0.0.1 or localhost."]
    # ensure_ascii=False: the brief states the command that runs, not a \u-escaped spelling of it.
    if runtime.health_command:
        ready = "it accepts connections" if runtime.kind == "database" else "it is ready"
        lines.append(f"Once {ready}, the health check command "
                     f"{json.dumps(list(runtime.health_command), ensure_ascii=False)}, run inside its container, "
                     f"must exit with status 0.")
    if runtime.start is not None:
        lines.append(f"Its container is built from {START_IMAGE} with the contents of components/{component_id}/ "
                     f"copied into /app, and the container runs the shell command "
                     f"{json.dumps(runtime.start, ensure_ascii=False)} in /app.")
    else:
        lines.append(f"Provide components/{component_id}/{runtime.dockerfile}; it is built with "
                     f"components/{component_id}/ as its build context, so it can copy only files from inside that "
                     f"directory, and its image must start the {runtime.kind}.")
    for dep in runtime.runtime_depends_on:
        target = runtimes[dep]
        variables = dependency_variables(dep, target)
        if target.kind == "service":
            (name, url), = variables.items()
            lines.append(f"Reach the {dep} service only through the base URL in the {name} environment variable "
                         f"(it will be {url}); never hard-code that host or port.")
        else:
            (host_name, host), (port_name, port) = variables.items()
            lines.append(f"Reach the {dep} database only through the host name in the {host_name} environment "
                         f"variable (it will be {host}) and the port in the {port_name} environment variable (it "
                         f"will be {port}); never hard-code that host or port.")
    if runtime.env:
        lines.append("Its container also gets these environment variables: "
                     + ", ".join(f"{name}={value}" for name, value in runtime.env) + ".")
    return lines


def require_runnable(component_ids: Iterable[str], runtimes: Mapping[str, ComponentRuntime | None]) -> None:
    """Running the combined system needs every component to say how it runs (a
    component that is never started declares {"kind": "library"}) and at least one
    service to check. Raises ValueError naming what is missing."""
    ids = list(component_ids)
    missing = [cid for cid in ids if runtimes.get(cid) is None]
    if missing:
        raise ValueError(f"every component needs a runtime block to run the combined system; missing on "
                         f"{', '.join(missing)} (a component that is never started declares {{\"kind\": \"library\"}})")
    if not any(runtimes[cid].is_service for cid in ids):
        raise ValueError("running the combined system needs at least one component of kind service")
