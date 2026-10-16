"""Exact Linux policies for the existing readonly Kilo client and pure SDK calls.

This module plans/replays kernel authority. The original Kilo owner still
owns configuration, seals, request accounting, source acknowledgements and
model/image acceptance. A policy file is never a passing model receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from . import autocode_native_linux_boundary as kernel
except ImportError:
    import autocode_native_linux_boundary as kernel

TRAMPOLINE = 'filter=$1; shift; exec "$@" 3< "$filter"'
KINDS = {"client": ("unix-relay", False), "derivation": ("none", True)}


def _require(value, reason):
    if not value:
        raise ValueError("Kilo Linux boundary: " + reason)


def policy(profile, *, kind, read_paths, write_paths, hidden_paths=(), cwd):
    _require(kind in KINDS, "unknown authority kind")
    profile = Path(profile)
    network, deny_fork = KINDS[kind]
    inputs = {
        "read_paths": list(dict.fromkeys(str(Path(p)) for p in read_paths)),
        "write_paths": list(dict.fromkeys(str(Path(p)) for p in write_paths)),
        "hidden_paths": list(dict.fromkeys(str(Path(p)) for p in hidden_paths)),
        "cwd": str(Path(cwd)),
    }
    program = kernel.seccomp_program(network=network, deny_fork=deny_fork)
    arguments = kernel.arguments(**inputs, seccomp_fd=3)
    if kind == "client":
        arguments.insert(1, "--as-pid-1")
        # Tool children must not obtain a write path to the compiled client's
        # memory through procfs while syscall-level ptrace is denied.
        arguments[-2:-2] = ["--remount-ro", "/proc"]
    return {
        "version": kernel.VERSION,
        "kind": kind,
        "inputs": inputs,
        "filter": {"path": str(profile.with_suffix(".bpf")), "sha256": hashlib.sha256(program).hexdigest()},
        "arguments": arguments,
    }


def text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def write(profile, value):
    profile = Path(profile)
    network, deny_fork = KINDS[value["kind"]]
    program = kernel.seccomp_filter(value["filter"]["path"], network=network, deny_fork=deny_fork)
    _require(hashlib.sha256(program.read_bytes()).hexdigest() == value["filter"]["sha256"], "filter export differs")
    with profile.open("x") as output:
        output.write(text(value))
    profile.chmod(0o400)
    return program


def verify(profile):
    profile = Path(profile)
    _require(
        profile.resolve(strict=True) == profile and profile.stat().st_mode & 0o222 == 0,
        "policy file is aliased or writable",
    )
    value = json.loads(profile.read_bytes())
    _require(set(value) == {"version", "kind", "inputs", "filter", "arguments"}, "unknown policy fields")
    inputs = value["inputs"]
    _require(
        isinstance(inputs, dict) and set(inputs) == {"read_paths", "write_paths", "hidden_paths", "cwd"},
        "unknown authority input fields",
    )
    expected = policy(profile, kind=value["kind"], **inputs)
    _require(value == expected and profile.read_text() == text(expected), "policy replay differs")
    network, deny_fork = KINDS[value["kind"]]
    program = Path(value["filter"]["path"])
    _require(
        program.resolve(strict=True) == program
        and program.stat().st_mode & 0o222 == 0
        and program.read_bytes() == kernel.seccomp_program(network=network, deny_fork=deny_fork),
        "original filter bytes or authority changed",
    )
    return value


def prefix(profile):
    value = verify(profile)
    return [
        "/bin/sh",
        "-c",
        TRAMPOLINE,
        "autocode-kilo-linux",
        value["filter"]["path"],
        str(kernel.executable()),
        *value["arguments"],
        "--",
    ]


def unwrap(command, profile):
    command = list(command)
    expected = prefix(profile)
    _require(command[: len(expected)] == expected and len(command) > len(expected), "command bypasses original policy")
    return command[len(expected) :]
