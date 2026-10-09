"""The Compose file that runs an architecture's components together on this machine.

It is generated from runtime blocks that autocode_component_runtime has already
validated (docs/task-lanes.md, "Declaring how a component runs") and written as
JSON, which Compose reads as it reads YAML (render says how). This module owns
its shape, which is also its security boundary, because every input in it may
have been written by a model:

- Each service takes keys only from ALLOWED_SERVICE_KEYS, so nothing can mount
  a volume or the Docker socket, run privileged, add capabilities or devices,
  share the host's network, process or IPC namespace, or read an env_file.
- Only a component of kind service publishes a port, and only on 127.0.0.1
  with an ephemeral host port ("127.0.0.1::PORT"), so ports may repeat across
  components and nothing listens beyond loopback. A worker has no port; a
  database is reached only by name over the project's own network.
- Every string value has "$" escaped as "$$", so Compose substitutes nothing
  from the host environment, and no environment value is left for the host to
  fill in. "$PORT" in a start command reaches the container's shell as written.
- Each component is built from its own directory, components/<id>/, never from
  another's. A start command runs in START_IMAGE through an inline Dockerfile
  that copies that directory to /app; anything else, and every database, is
  built from its own Dockerfile.
- Every container gets an init process, no-new-privileges, and memory and
  process limits.

Health: a service's HTTP health path is probed from the host on its published
port, so it has no Compose healthcheck. A worker's or a database's health
command becomes its healthcheck, which `docker compose ps` reports as its
Health. depends_on gives each runtime dependency the condition service_healthy
when it has a health command and service_started otherwise, but Compose waits
on those conditions only when it starts the dependencies itself: `up --no-deps`
ignores them. So a runner that starts one start_layers layer at a time with
--no-deps must itself wait until every health-checked container of a layer
reports Health "healthy" (`ps --format json`) before it starts the next layer.

There is no top-level "name": the project name is passed with -p, which keeps
the rendered file the same for the same inputs. Pure: builds a dict and renders
it; runs nothing and writes no file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

try:
    from .autocode_component_runtime import START_IMAGE, ComponentRuntime, service_environment, start_layers
except ImportError:
    from autocode_component_runtime import START_IMAGE, ComponentRuntime, service_environment, start_layers

ALLOWED_SERVICE_KEYS = frozenset(
    {
        "build",
        "command",
        "depends_on",
        "environment",
        "healthcheck",
        "init",
        "mem_limit",
        "pids_limit",
        "ports",
        "security_opt",
    }
)
# Keys that would reach past the container. None is ever generated; the tests walk every document for them.
FORBIDDEN_KEYS = frozenset(
    {
        "privileged",
        "volumes",
        "volumes_from",
        "network_mode",
        "cap_add",
        "devices",
        "pid",
        "ipc",
        "userns_mode",
        "extra_hosts",
        "secrets",
        "env_file",
        "deploy",
    }
)
MEM_LIMIT = "2g"
PIDS_LIMIT = 1024
SECURITY_OPT = ("no-new-privileges:true",)
# dockerfile_inline needs Compose 2.17 or newer.
INLINE_DOCKERFILE = "FROM {image}\nWORKDIR /app\nCOPY . /app\n"
# A health command runs about every second; this many failures in a row, about two
# minutes, mark the container unhealthy.
HEALTH_INTERVAL = "1s"
HEALTH_TIMEOUT = "5s"
HEALTH_RETRIES = 120
# In json.dumps output: an escaped backslash, matched first so the text after it is never
# read as an escape, or the surrogate-pair escape of one character above U+FFFF.
_ESCAPE = re.compile(r"\\\\|\\u(d[89ab][0-9a-f]{2})\\u(d[c-f][0-9a-f]{2})")
_SURROGATE = re.compile(r"[\ud800-\udfff]")


def escape(value: object) -> object:
    """``value`` with "$" written as "$$" in every string, recursively, so Compose
    interpolates nothing. Keys are left as they are: they are ids and names that
    cannot contain "$"."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escape(item) for item in value]
    if isinstance(value, dict):
        return {key: escape(item) for key, item in value.items()}
    return value


def compose_document(runtimes: Mapping[str, ComponentRuntime | None], tree: Path) -> dict:
    """The Compose document for every component that runs (service, worker or
    database), built from the combined tree at ``tree``, an absolute path whose
    components/<id>/ directories hold each component's code. ``runtimes`` maps
    component ids to their blocks; libraries and components without one are left
    out. Raises ValueError for a relative tree, a tree path that is not valid
    UTF-8, or a runtime dependency that start_layers refuses."""
    tree = Path(tree)
    if not tree.is_absolute():
        raise ValueError(f"the combined tree must be an absolute path (found {str(tree)!r})")
    if _SURROGATE.search(str(tree)):
        raise ValueError(
            f"the combined tree's path {str(tree)!r} has bytes that are not UTF-8, which the Compose "
            f"file cannot carry; use a workspace whose path is valid UTF-8"
        )
    start_layers(runtimes)
    services = {
        cid: _service(cid, runtime, runtimes, tree)
        for cid, runtime in sorted(runtimes.items())
        if runtime is not None and runtime.runs
    }
    return escape({"services": services})


def render(document: dict) -> str:
    """The exact text to write to compose.json, encoded as UTF-8.

    Every non-ASCII character up to U+FFFF is a JSON \\u escape, so nothing Compose's
    YAML reader refuses written raw (U+FFFE, U+FFFF, C1 controls) is ever raw. Every
    character above U+FFFF is written as itself, because that reader refuses the
    surrogate-pair escape JSON otherwise uses for it. So this is not what
    autocode_util.atomic_json writes, which escapes such a character as a pair:
    write this text instead. A lone surrogate would stay an escape Compose
    refuses, so compose_document and the runtime block's validation refuse it."""
    return _ESCAPE.sub(_unescaped, json.dumps(document, indent=2, sort_keys=True) + "\n")


def _unescaped(match: re.Match) -> str:
    if match.group(1) is None:
        return match.group(0)
    high, low = int(match.group(1), 16), int(match.group(2), 16)
    return chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))


def _service(cid: str, runtime: ComponentRuntime, runtimes: Mapping[str, ComponentRuntime | None], tree: Path) -> dict:
    context = str(tree / "components" / cid)
    service: dict = {
        "environment": service_environment(cid, runtimes),
        "init": True,
        "mem_limit": MEM_LIMIT,
        "pids_limit": PIDS_LIMIT,
        "security_opt": list(SECURITY_OPT),
    }
    if runtime.start is not None:
        service["build"] = {"context": context, "dockerfile_inline": INLINE_DOCKERFILE.format(image=START_IMAGE)}
        service["command"] = ["/bin/sh", "-c", runtime.start]
    else:
        service["build"] = {"context": context, "dockerfile": runtime.dockerfile}
    if runtime.is_service:
        service["ports"] = [f"127.0.0.1::{runtime.port}"]
    if runtime.health_command:
        service["healthcheck"] = {
            "test": ["CMD", *runtime.health_command],
            "interval": HEALTH_INTERVAL,
            "timeout": HEALTH_TIMEOUT,
            "retries": HEALTH_RETRIES,
        }
    if runtime.runtime_depends_on:
        service["depends_on"] = {
            dep: {"condition": "service_healthy" if runtimes[dep].health_command else "service_started"}
            for dep in sorted(runtime.runtime_depends_on)
        }
    return service
