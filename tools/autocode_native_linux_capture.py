"""Linux capture policy and exact inherited-channel branch commands.

Trusted bootstrap, browser and supplied fixture remain separate processes.
The latter two receive a readonly copy, exact runtime files, owned writable
scratch and existing descriptor capabilities. New connections/binds/listeners
are denied by seccomp, including loopback inside their private namespaces.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

try:
    from . import autocode_native_linux_boundary as kernel
except ImportError:
    import autocode_native_linux_boundary as kernel


TRANSPORT = "linux-bwrap-inherited-cdp-v1"


def _require(condition, reason):
    if not condition:
        raise ValueError("Native Linux capture: " + reason)


def executable_pins():
    paths = (kernel.executable(), Path("/bin/sh").resolve(strict=True))
    pins = {}
    for path in paths:
        info = path.stat()
        _require(
            path.resolve(strict=True) == path
            and stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_mode & 0o022 == 0
            and os.access(path, os.X_OK),
            "fixed kernel trampoline is not administrator controlled",
        )
        pins[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return pins


def policy(scratch, runtime, *, inherited_fds, listener_fd):
    scratch = Path(scratch)
    _require(
        len(set(inherited_fds)) == len(inherited_fds) and all(type(fd) is int and fd >= 3 for fd in inherited_fds),
        "original inherited descriptor inventory required",
    )
    _require(listener_fd in inherited_fds, "listener is not an original inherited capability")
    # The fixed shell's portable redirection syntax supports single-digit FDs.
    available = [fd for fd in range(5, 10) if fd not in inherited_fds]
    _require(available, "no distinct private filter descriptor slot")
    fd = available[0]
    engine = Path(__file__).parent.resolve()
    reads = tuple(
        dict.fromkeys(
            (
                scratch,
                *(Path(name) for name in runtime["files"]),
                engine / "autocode_visual_browser.cjs",
                engine / "autocode_native_capture_browser.cjs",
            )
        )
    )
    return {
        "version": kernel.VERSION,
        "kind": "native-capture",
        "transport": TRANSPORT,
        "listener_fd": listener_fd,
        "filter": {
            "path": str(scratch.parent / "capture-browser.bpf"),
            "sha256": hashlib.sha256(kernel.seccomp_program()).hexdigest(),
            "fd": fd,
        },
        "fixture_filter": {
            "path": str(scratch.parent / "capture-fixture.bpf"),
            "sha256": hashlib.sha256(kernel.seccomp_program(inherited_listener=listener_fd)).hexdigest(),
            "fd": fd,
        },
        "trampoline": str(Path("/bin/sh").resolve(strict=True)),
        "kernel": str(kernel.executable()),
        "arguments": kernel.arguments(
            read_paths=reads,
            write_paths=(scratch / "writable",),
            cwd=scratch / "source",
            seccomp_fd=fd,
            inherited_fds=tuple(sorted({3, 4, *inherited_fds})),
        ),
    }


def text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def write(scratch, runtime, *, inherited_fds, listener_fd):
    value = policy(scratch, runtime, inherited_fds=inherited_fds, listener_fd=listener_fd)
    kernel.seccomp_filter(value["filter"]["path"])
    kernel.seccomp_filter(value["fixture_filter"]["path"], inherited_listener=listener_fd)
    return value


def verify_program(value):
    for key, descriptor in (("filter", None), ("fixture_filter", value["listener_fd"])):
        path = Path(value[key]["path"])
        _require(
            path.resolve(strict=True) == path
            and path.is_file()
            and path.stat().st_mode & 0o222 == 0
            and path.read_bytes() == kernel.seccomp_program(inherited_listener=descriptor),
            "original capture seccomp bytes changed",
        )
