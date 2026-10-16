"""Exact Linux readonly-check policy bytes and replay; no launch authority.

The existing native check owner supplies its authenticated copy, approved
command and environment. Its ordinary parent sink and held launch stay the
only authority for admission, process ownership and completed proof.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from . import autocode_native_linux_boundary as kernel
    from . import autocode_util as util
except ImportError:
    import autocode_native_linux_boundary as kernel
    import autocode_util as util


TRAMPOLINE = 'filter=$1; shift; exec "$@" 3< "$filter"'


def _require(condition, reason):
    if not condition:
        raise ValueError("Native Linux check: " + reason)


def policy(boundary, *, runtime):
    """Rebuild only this authenticated check's fixed readonly mount inputs."""
    scratch, tree = Path(boundary["scratch"]), Path(boundary["tree"])
    profile = Path(boundary["profile"])
    _require(profile.parent == scratch.parent and profile.suffix == ".json", "foreign policy file")
    program = profile.with_suffix(".bpf")
    roots = [Path(path) for path in boundary["runtime_roots"]]
    if runtime is not None:
        roots.extend(Path(path) for path in runtime["files"])
    reads = tuple(dict.fromkeys((scratch, *roots)))
    writable = tree / ".git" if boundary["setup"] else scratch / "writable"
    _require(writable.is_dir(), "original writable destination is missing")
    arguments = kernel.arguments(read_paths=reads, write_paths=(writable,), cwd=tree, seccomp_fd=3)
    if runtime is not None and runtime["logical_node"]["path"] != runtime["node"]:
        # The original owner already authenticated this one logical alias.
        # Recreate only it; never expose its parent directory as a read root.
        arguments[-2:-2] = ["--symlink", runtime["node"], runtime["logical_node"]["path"]]
    return {
        "version": kernel.VERSION,
        "kind": "readonly-check",
        "filter": {"path": str(program), "sha256": hashlib.sha256(kernel.seccomp_program()).hexdigest()},
        "arguments": arguments,
    }


def text(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def write(boundary, *, runtime):
    profile = Path(boundary["profile"])
    value = policy(boundary, runtime=runtime)
    program = kernel.seccomp_filter(value["filter"]["path"])
    _require(util.file_hash(program) == value["filter"]["sha256"], "exported filter differs")
    with profile.open("x") as output:
        output.write(text(value))
    profile.chmod(0o400)
    return value


def command(value, approved_command):
    _require(isinstance(approved_command, str) and approved_command, "approved command required")
    return [
        "/bin/sh",
        "-c",
        TRAMPOLINE,
        "autocode-native-readonly",
        value["filter"]["path"],
        str(kernel.executable()),
        *value["arguments"],
        "--",
        "/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        approved_command,
    ]


def verify(boundary, body, *, runtime):
    profile = Path(boundary["profile"])
    expected = policy(boundary, runtime=runtime)
    program = Path(expected["filter"]["path"])
    _require(
        profile.read_text() == text(expected)
        and util.file_hash(profile) == boundary["profile_sha256"]
        and program.is_file()
        and program.resolve(strict=True) == program
        and program.stat().st_mode & 0o222 == 0
        and program.read_bytes() == kernel.seccomp_program(),
        "original readonly policy or actual seccomp program changed",
    )
    actual = body["command"]
    _require(
        isinstance(actual, list)
        and actual
        and actual == command(expected, actual[-1])
        and body["command_sha256"] == util.digest(actual),
        "command bypasses original Linux kernel boundary",
    )
