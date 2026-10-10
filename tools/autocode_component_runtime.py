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
obligations to trace, not literals.

The architecture directory may also hold smoke.json, the end-to-end smoke
check of the combined system (docs/task-lanes.md, "Declaring the smoke
check"): HTTP requests to services, in order, with expected statuses and JSON,
and values captured from one response for later requests. It is not a build
input: it is outside the saved build's identity, so editing it never forces a
rebuild. load_smoke reads and validates it, check_smoke checks it against the
runtime blocks, and render_step fills in captured values. Nothing in it is
ever run as a command.

Pure: runs nothing, and reads no file except smoke.json in load_smoke.
Imports nothing from AutoCode, so autocode_multicomponent can import it
without joining an import cycle.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import difflib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

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
CONTAINER_LOCAL_HOSTS = frozenset(
    {"localhost", "ip6-localhost", "ip6-loopback", "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters"}
)
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
            raise ValueError('runtime must be a JSON object such as {"kind": "library"}')
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
            raise ValueError(
                f"a {kind}'s component id must be a lowercase DNS label that starts with a letter "
                f"(a-z, digits and '-', at most 63 characters, not ending in '-'), because it "
                f"becomes its container's host name and the stem of environment variable names "
                f"(found {component_id!r})"
            )
        if component_id in CONTAINER_LOCAL_HOSTS:
            raise ValueError(
                f"a {kind} cannot have the id {component_id!r}: inside every container that name "
                f"means the container itself, so a component connecting to it would reach itself; "
                f"choose another id"
            )
        port = _port(block, kind)
        start, dockerfile = _build(block, kind, component_id)
        health, health_command = _health(block, kind)
        depends_on = _depends_on(block)
        env = _env(block, depends_on)
        return cls(
            kind=kind,
            port=port,
            start=start,
            dockerfile=dockerfile,
            health=health,
            health_command=health_command,
            runtime_depends_on=depends_on,
            env=env,
        )


def _not_for_kind(kind: str, key: str) -> str:
    if kind == "library":
        return f"a library is never started, so its runtime block carries only kind, not {key!r}"
    if kind == "worker" and key == "port":
        return "a worker has no port; a component that answers HTTP requests is a service"
    if kind == "database" and key == "start":
        return (
            f"a database needs a dockerfile, not start: a start command runs in {START_IMAGE}, "
            f"which cannot run a database"
        )
    return f"a {kind} does not take {key!r}"


def _text(key: str, value, *, limit: int = MAX_TEXT, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string (found {value!r})")
    if not value and not empty:
        raise ValueError(f"{key} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{key} must be at most {limit} characters")
    if any(char in value for char in "\0" + LINE_BREAKS):
        raise ValueError(
            f"{key} must be a single line without NUL or line-break characters (CR, LF, VT, FF, "
            f"NEL, U+2028, U+2029 and the like)"
        )
    if "`" in value:
        raise ValueError(f"{key} must not contain a backtick (`)")
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise ValueError(
            f"{key} must not contain a lone surrogate (U+D800 to U+DFFF), which is half of a "
            f"character's escape and not a character"
        )
    return value


def _port(block: dict, kind: str) -> int | None:
    if "port" not in block:
        if kind == "service":
            raise ValueError("a service needs port, the container port it listens on (1-65535)")
        if kind == "database":
            raise ValueError(
                "a database needs port, the container port it accepts connections on (1-65535): it "
                "is never published, but the components that connect to it are told this port"
            )
        return None
    port = block["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"port must be an integer from 1 to 65535 (found {port!r})")
    return port


def _build(block: dict, kind: str, component_id: str) -> tuple[str | None, str | None]:
    """(start, dockerfile): exactly one, and a database always has a dockerfile."""
    has_start, has_dockerfile = "start" in block, "dockerfile" in block
    if kind == "database" and not has_dockerfile:
        raise ValueError(
            f"a database needs dockerfile, the Dockerfile inside components/{component_id}/ that builds and starts it"
        )
    if has_start == has_dockerfile:
        raise ValueError(
            f"a {kind} needs exactly one of start (a shell command run in {START_IMAGE}) or "
            f"dockerfile (a Dockerfile inside components/{component_id}/)"
        )
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
        raise ValueError(
            f"dockerfile must be a relative path inside components/{component_id}/ made of '/'-separated "
            f"segments of letters, digits, '.', '_' and '-', with no empty, '.' or '..' segment "
            f"(found {dockerfile!r})"
        )
    return None, dockerfile


def _health(block: dict, kind: str) -> tuple[str | None, tuple[str, ...]]:
    """(health path, health command): a service's is an HTTP path; a worker's or a
    database's is a command run inside its container, optional for a worker."""
    if "health" not in block:
        if kind == "service":
            raise ValueError(
                'a service needs health, the HTTP path that answers 2xx once it is ready, such as "/health"'
            )
        if kind == "database":
            raise ValueError(
                "a database needs health, a command run inside its container that exits 0 once it "
                'accepts connections, as a JSON array of strings such as ["pg_isready"]'
            )
        return None, ()
    health = block["health"]
    if kind == "service":
        if not isinstance(health, str) or not health.startswith("/"):
            raise ValueError(f"a service's health must be a URL path starting with '/' (found {health!r})")
        if ".." in health:
            raise ValueError(f"health must not contain '..' (found {health!r})")
        if not HEALTH_PATH.fullmatch(health):
            raise ValueError(
                f"health may contain only letters, digits and '.', '_', '~', '/', '-', at most 128 "
                f"characters in all (found {health!r})"
            )
        return health, ()
    if not isinstance(health, list) or not 1 <= len(health) <= MAX_COMMAND:
        raise ValueError(
            f"a {kind}'s health must be a command run inside its container, a JSON array of 1 to "
            f'{MAX_COMMAND} strings such as ["python3", "check.py"] (found {health!r})'
        )
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
            raise ValueError(
                f"env name {name!r} must be upper case: letters A-Z, digits and '_', not starting "
                f"with a digit, at most 64 characters"
            )
        if name == "PORT":
            raise ValueError("env name PORT is reserved: it carries this component's port")
        if name in generated:
            raise ValueError(
                f"env name {name} is reserved: it carries the address of the runtime dependency {generated[name]}"
            )
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
                raise ValueError(
                    f"{cid} runtime_depends_on {dep}, a worker, which has no address to connect to; "
                    f"only a service or a database can be a runtime dependency"
                )
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
    assert runtime is not None
    environment = {"PORT": str(runtime.port)} if runtime.port is not None else {}
    for dep in runtime.runtime_depends_on:
        dependency = runtimes[dep]
        assert dependency is not None
        environment.update(dependency_variables(dep, dependency))
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
        lines = [
            f"When the combined system runs, this component runs as a long-lived HTTP service in its own "
            f"container: it must listen on 0.0.0.0, not only on 127.0.0.1 or localhost, at the port in the "
            f"PORT environment variable (which will be {runtime.port}), and answer GET {runtime.health} with "
            f"a 2xx status once it is ready."
        ]
    elif runtime.kind == "worker":
        lines = [
            "When the combined system runs, this component runs as a long-running worker process in its own "
            "container, with no HTTP port and no published port: nothing connects to it, and it must keep "
            "running until it is stopped rather than exit when it is idle or its work is done."
        ]
    else:
        lines = [
            f"When the combined system runs, this component runs as a database in its own container: other "
            f"components reach it only over the combined system's internal network, never through a "
            f"published port, and it must accept connections on port {runtime.port} (also given in the PORT "
            f"environment variable), listening on 0.0.0.0, not only on 127.0.0.1 or localhost."
        ]
    # ensure_ascii=False: the brief states the command that runs, not a \u-escaped spelling of it.
    if runtime.health_command:
        ready = "it accepts connections" if runtime.kind == "database" else "it is ready"
        lines.append(
            f"Once {ready}, the health check command "
            f"{json.dumps(list(runtime.health_command), ensure_ascii=False)}, run inside its container, "
            f"must exit with status 0."
        )
    if runtime.start is not None:
        lines.append(
            f"Its container is built from {START_IMAGE} with the contents of components/{component_id}/ "
            f"copied into /app, and the container runs the shell command "
            f"{json.dumps(runtime.start, ensure_ascii=False)} in /app."
        )
    else:
        lines.append(
            f"Provide components/{component_id}/{runtime.dockerfile}; it is built with "
            f"components/{component_id}/ as its build context, so it can copy only files from inside that "
            f"directory, and its image must start the {runtime.kind}."
        )
    for dep in runtime.runtime_depends_on:
        target = runtimes[dep]
        assert target is not None
        variables = dependency_variables(dep, target)
        if target.kind == "service":
            ((name, url),) = variables.items()
            lines.append(
                f"Reach the {dep} service only through the base URL in the {name} environment variable "
                f"(it will be {url}); never hard-code that host or port."
            )
        else:
            (host_name, host), (port_name, port) = variables.items()
            lines.append(
                f"Reach the {dep} database only through the host name in the {host_name} environment "
                f"variable (it will be {host}) and the port in the {port_name} environment variable (it "
                f"will be {port}); never hard-code that host or port."
            )
    if runtime.env:
        lines.append(
            "Its container also gets these environment variables: "
            + ", ".join(f"{name}={value}" for name, value in runtime.env)
            + "."
        )
    return lines


def require_runnable(component_ids: Iterable[str], runtimes: Mapping[str, ComponentRuntime | None]) -> None:
    """Running the combined system needs every component to say how it runs (a
    component that is never started declares {"kind": "library"}) and at least one
    service to check. Raises ValueError naming what is missing."""
    ids = list(component_ids)
    missing = [cid for cid in ids if runtimes.get(cid) is None]
    if missing:
        raise ValueError(
            f"every component needs a runtime block to run the combined system; missing on "
            f'{", ".join(missing)} (a component that is never started declares {{"kind": "library"}})'
        )
    if not any(runtime is not None and runtime.is_service for runtime in (runtimes[cid] for cid in ids)):
        raise ValueError("running the combined system needs at least one component of kind service")


def architecture_contract(task: str) -> dict:
    """Expose the validated runtime grammar to a requested runnable architecture."""
    if "components.json" not in task or not re.search(r"\bruntime\b|\bsmoke\b|\bcompose\b", task, re.I):
        return {}
    return {
        "runtime_kinds": {kind: sorted({"kind", *KIND_KEYS[kind]}) for kind in KINDS},
        "start_image": START_IMAGE,
        "smoke_file": SMOKE_FILE,
        "smoke_keys": list(SMOKE_KEYS),
        "smoke_step_keys": list(STEP_KEYS),
        "smoke_methods": list(METHODS),
        "instruction": prompts.get("fragments/component-runtime/architecture-contract.md"),
    }


# The smoke check: ARCHITECTURE/smoke.json, beside components.json. Read only to run
# the combined system; never part of the saved build's identity.
SMOKE_FILE = "smoke.json"
SMOKE_KEYS = ("version", "steps")
STEP_KEYS = ("name", "service", "method", "path", "body", "expect_status", "expect_json", "capture")
MAX_STEPS = 30
MAX_STEP_NAME = 80
MAX_PATH = 300
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODYLESS_METHODS = frozenset({"GET", "DELETE"})
# A value captured from one step's response, used as {{name}} in a later step's path or body.
VAR_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]{0,31})\}\}")
# Why a smoke step cannot send a request to a component of each other kind.
UNREACHABLE = {
    "library": "a library, which is never started",
    "worker": "a worker, which has no HTTP port",
    "database": "a database, whose port is never published; check it through a service that uses it",
}


@dataclass(frozen=True)
class SmokeStep:
    """One HTTP request of the smoke check. has_body and has_expect_json tell an absent
    key from an explicit JSON null; capture is (variable, top-level response key) pairs."""

    name: str
    service: str
    method: str
    path: str
    expect_status: int
    body: object = None
    has_body: bool = False
    expect_json: object = None
    has_expect_json: bool = False
    capture: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SmokeCheck:
    steps: tuple[SmokeStep, ...]
    path: Path


def load_smoke(architecture_dir) -> SmokeCheck:
    """Read and validate ARCHITECTURE/smoke.json. Raises ValueError naming the step and
    the key. Whether each step's service is one that can be reached is check_smoke's job."""
    path = Path(architecture_dir) / SMOKE_FILE
    if not path.is_file():
        raise ValueError(
            f"missing {path}: running the combined system needs a smoke check, the HTTP requests "
            f'that show it works (docs/task-lanes.md, "Declaring the smoke check")'
        )
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_not_json,
            parse_float=_finite_float,
        )
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read {path}: {error}") from None
    except ValueError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from None
    return SmokeCheck(steps=_smoke_steps(document), path=path)


def check_smoke(smoke: SmokeCheck, runtimes: Mapping[str, ComponentRuntime | None]) -> None:
    """Every step must send its request to a service, the only kind with a published HTTP
    port. ``runtimes`` maps component ids to their blocks. Raises ValueError."""
    for index, step in enumerate(smoke.steps, 1):
        where = _step_label(index, step.name)
        if step.service not in runtimes:
            raise ValueError(f"{where} service {step.service!r} is not a component that declares a runtime block")
        target = runtimes[step.service]
        if target is None:
            raise ValueError(f"{where} service {step.service!r} declares no runtime block")
        if not target.is_service:
            raise ValueError(
                f"{where} service {step.service!r} is {UNREACHABLE[target.kind]}; a smoke step can "
                f"send requests only to a component of kind service"
            )


def render_step(step: SmokeStep, captured: Mapping[str, str | int]) -> tuple[str, object]:
    """The step's path and body with each {{name}} filled in from ``captured``.

    In the path a value is percent-encoded, '/' included, so it stays within one path
    segment or query value. In the body, a string that is exactly {{name}} becomes the
    captured value itself, so an integer stays an integer; inside a longer string the
    value is inserted as text. The body is None when the step has none, and the step
    itself is never changed. Raises ValueError for a value that was not captured."""

    def value(name: str) -> str | int:
        if name not in captured:
            raise ValueError(f"{step.name}: {_placeholder(name)} was not captured")
        found = captured[name]
        if isinstance(found, bool) or not isinstance(found, (str, int)):
            raise ValueError(f"{step.name}: the captured {name} must be a string or an integer (found {found!r})")
        return found

    path = PLACEHOLDER.sub(lambda match: quote(str(value(match.group(1))), safe=""), step.path)
    return path, (_filled(step.body, value) if step.has_body else None)


def _filled(node, value):
    if isinstance(node, str):
        whole = PLACEHOLDER.fullmatch(node)
        if whole:
            return value(whole.group(1))
        return PLACEHOLDER.sub(lambda match: str(value(match.group(1))), node)
    if isinstance(node, list):
        return [_filled(item, value) for item in node]
    if isinstance(node, dict):
        return {key: _filled(item, value) for key, item in node.items()}
    return node


def _smoke_steps(document) -> tuple[SmokeStep, ...]:
    if not isinstance(document, dict):
        raise ValueError(
            f'{SMOKE_FILE} must be a JSON object such as {{"version": 1, "steps": [...]}} (found {_shown(document)})'
        )
    _known_keys(document, SMOKE_KEYS, SMOKE_FILE)
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError(f"{SMOKE_FILE} version must be 1 (found {_shown(version)})")
    steps = document.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        found = f"{len(steps)} steps" if isinstance(steps, list) else _shown(steps)
        raise ValueError(f"{SMOKE_FILE} steps must be a list of 1 to {MAX_STEPS} steps (found {found})")
    captured: dict[str, int] = {}  # variable -> the step that captures it
    named: dict[str, int] = {}
    parsed = []
    for index, raw in enumerate(steps, 1):
        step = _smoke_step(index, raw, captured)
        if step.name in named:
            raise ValueError(
                f"{SMOKE_FILE} steps {named[step.name]} and {index} are both named {step.name!r}; "
                f"step names must be unique"
            )
        named[step.name] = index
        parsed.append(step)
    return tuple(parsed)


def _smoke_step(index: int, raw, captured: dict[str, int]) -> SmokeStep:
    where = _step_label(index, None)
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a JSON object (found {_shown(raw)})")
    name = raw.get("name", f"step {index}")
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_STEP_NAME or not name.isprintable():
        raise ValueError(
            f"{where} name must be a string of 1 to {MAX_STEP_NAME} printable characters on one line "
            f"(found {_shown(name)})"
        )
    _no_placeholders(name, f"{where} name")
    where = _step_label(index, name)
    _known_keys(raw, STEP_KEYS, where)
    for key in ("service", "method", "path", "expect_status"):
        if key not in raw:
            raise ValueError(f"{where} needs {key}")
    service = raw["service"]
    if not isinstance(service, str) or not service:
        raise ValueError(f"{where} service must be the id of a component of kind service (found {_shown(service)})")
    method = raw["method"]
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError(f"{where} method must be one of {', '.join(METHODS)} (found {_shown(method)})")
    path = _request_path(raw["path"], where, captured)
    status = raw["expect_status"]
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise ValueError(
            f"{where} expect_status must be an integer HTTP status from 100 to 599 (found {_shown(status)})"
        )
    if "body" in raw:
        if method in BODYLESS_METHODS:
            raise ValueError(
                f"{where} is a {method} request, which sends no body; remove body or use POST, PUT or PATCH"
            )
        _check_placeholders(raw["body"], f"{where} body", captured)
    if "expect_json" in raw:
        _no_placeholders(raw["expect_json"], f"{where} expect_json")
    capture = _capture(raw.get("capture", {}), where, captured)
    captured.update((variable, index) for variable, _ in capture)
    return SmokeStep(
        name=name,
        service=service,
        method=method,
        path=path,
        expect_status=status,
        body=raw.get("body"),
        has_body="body" in raw,
        expect_json=raw.get("expect_json"),
        has_expect_json="expect_json" in raw,
        capture=capture,
    )


def _request_path(path, where: str, captured: Mapping[str, int]) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{where} path must start with '/' (found {_shown(path)})")
    if len(path) > MAX_PATH:
        raise ValueError(f"{where} path must be at most {MAX_PATH} characters")
    if not path.isascii() or any(char.isspace() or not char.isprintable() for char in path):
        raise ValueError(
            f"{where} path must be printable ASCII with no spaces; percent-encode anything else (found {_shown(path)})"
        )
    if "://" in path or "@" in path:
        raise ValueError(
            f"{where} path must be a path on the step's service, with no '://' or '@' (found {_shown(path)})"
        )
    _check_placeholders(path, f"{where} path", captured)
    return path


def _check_placeholders(value, where: str, captured: Mapping[str, int]) -> None:
    """Each {{name}} in a string of ``value`` must be captured by an earlier step. An
    object key is sent as written, so it may hold no '{{' at all."""
    if isinstance(value, str):
        if "{{" in PLACEHOLDER.sub("", value):
            raise ValueError(
                f"{where}: '{{{{' may only open a captured variable such as {_placeholder('note_id')} "
                f"(lower case, at most 32 characters; found {_shown(value)})"
            )
        for name in PLACEHOLDER.findall(value):
            if name not in captured:
                raise ValueError(f"{where} uses {_placeholder(name)}, but no earlier step captures {name}")
    elif isinstance(value, list):
        for item in value:
            _check_placeholders(item, where, captured)
    elif isinstance(value, dict):
        for key, item in value.items():
            _no_placeholders(key, f"{where} key")
            _check_placeholders(item, where, captured)


def _no_placeholders(value, where: str) -> None:
    """No string in ``value``, object keys included, may contain '{{': render_step fills
    in captured values only in the path and in string values of the body."""
    if isinstance(value, str):
        if "{{" in value:
            raise ValueError(
                f"{where} must not contain '{{{{' (found {_shown(value)}): captured values are filled "
                f"in only in the path and in string values of the body"
            )
    elif isinstance(value, list):
        for item in value:
            _no_placeholders(item, where)
    elif isinstance(value, dict):
        for key, item in value.items():
            _no_placeholders(key, where)
            _no_placeholders(item, where)


def _capture(capture, where: str, captured: Mapping[str, int]) -> tuple[tuple[str, str], ...]:
    if not isinstance(capture, dict):
        raise ValueError(
            f"{where} capture must be a JSON object of variable names to top-level response keys, "
            f'such as {{"note_id": "id"}} (found {_shown(capture)})'
        )
    for variable, key in capture.items():
        if not VAR_NAME.fullmatch(variable):
            raise ValueError(
                f"{where} capture variable {variable!r} must be lower case: a-z, digits and '_', "
                f"starting with a letter, at most 32 characters"
            )
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"{where} capture {variable} must name a top-level key of the JSON response (found {_shown(key)})"
            )
        if variable in captured:
            raise ValueError(
                f"{where} captures {variable}, which step {captured[variable]} already captures; give it another name"
            )
    return tuple(capture.items())


def _known_keys(document: dict, allowed: tuple[str, ...], where: str) -> None:
    for key in sorted(document):
        if key not in allowed:
            close = difflib.get_close_matches(key, allowed, n=1, cutoff=0.6)
            raise ValueError(f"{where}: unknown key {key!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))


def _step_label(index: int, name: str | None) -> str:
    """How a message names a step: by number, and by its name when it has its own."""
    if name is None or name == f"step {index}":
        return f"{SMOKE_FILE} step {index}"
    return f"{SMOKE_FILE} step {index} ({name})"


def _placeholder(name: str) -> str:
    return "{{" + name + "}}"


def _shown(value) -> str:
    shown = repr(value)
    return shown if len(shown) <= 80 else shown[:77] + "..."


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"the key {key!r} appears twice in one object")
        seen.add(key)
    return dict(pairs)


def _not_json(constant: str):
    raise ValueError(f"{constant} is not a JSON value")


def _finite_float(text: str) -> float:
    """A JSON number with a fraction or exponent; one too large for a float, such as
    1e999, would become infinity, which no request can send as JSON."""
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"the number {text} is too large to send as JSON")
    return number
