"""Matching executed command events against the commands a Validator report claims.

A report cites an executed event by id, or a bare command that must match the
recorded event line: Codex may record the command wrapped in a login shell, and
workspace runs prepend `cd <workspace> &&` and a stderr redirect. Pure functions
over recorded strings; this module sits below autocode_support and imports
nothing from AutoCode.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

_ZSH_WRAPPER = re.compile(r"^\S*/zsh\s+(?:-lc|-l\s+-c)\s+(.+)$", re.S)


def _command_bodies(command):
    """Candidate unwrapped bodies for a recorded or reported command line.
    Shlex-unwraps whole zsh shell wrappers when quoting is well-formed; also
    offers the line with one stray trailing quote removed, which codex event
    recording has been observed to leave behind on nested-quote commands.
    The wrapper is only unwrapped when it accounts for the whole line:
    anything after the body (an operator chain, or extra arguments that
    become the shell's positional parameters) is executable content, and
    dropping it would make a different program look identical."""
    variants = [command]
    stripped = command.rstrip()
    if stripped and stripped[-1] in "\"'":
        variants.append(stripped[:-1])
    bodies = []
    for variant in variants:
        try:
            parts = shlex.split(variant)
        except ValueError:
            parts = None
        if parts and parts[0].endswith("/zsh"):
            if parts[1:2] == ["-c"] and len(parts) == 3 and variant == command:
                bodies.append(parts[2])
                continue
            if parts[1:2] == ["-lc"] and len(parts) == 3:
                bodies.append(parts[2])
                continue
            if parts[1:3] == ["-l", "-c"] and len(parts) == 4:
                bodies.append(parts[3])
                continue
        if parts is None:
            match = _ZSH_WRAPPER.match(variant.strip())
            if match:
                bodies.append(match.group(1))
                continue
        bodies.append(variant)
    return bodies


def same_command(event_command, check_command):
    """Codex may record the model command wrapped in zsh (-c, -lc or -l -c).
    Normalize the wrapper on either side: the event is recorded wrapped, and a report
    may quote the event line verbatim (wrapper included) or as the bare command."""
    if event_command == check_command:
        return True
    for event_body in _command_bodies(event_command):
        for check_body in _command_bodies(check_command):
            # Compare the shell program text. Token equality drops quotes, so a
            # command that prints an operator can look identical to one that
            # executes it (`printf '%s\n' '&&' false` versus `printf '%s\n' && false`).
            if event_body == check_body:
                return True
    return False


def workspace_wrapped_command(executed, reported, workspace):
    """Match a bare command to the recorded workspace wrapper and stderr redirect."""
    prefix = f"cd {shlex.quote(str(Path(workspace).resolve()))} && "
    if not executed.startswith(prefix):
        return False
    body = executed[len(prefix) :]
    if body.endswith(" 2>&1"):
        body = body[:-5]
    return body == reported


def ambiguous_native_command_ids(text):
    """IDs with conflicting completed native commands; repeated updates are one part.

    Normalization keeps the final update for each part. Hydrating an omitted
    command must not let that hide conflicting execution facts under the same ID.
    """
    identities = {}
    ambiguous = set()
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "tool_use":
            continue
        part = row.get("part")
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            continue
        key = part["id"]
        if part.get("sessionID") not in (None, row.get("sessionID")):
            ambiguous.add(key)
        state = part.get("state")
        if (
            not isinstance(state, dict)
            or state.get("status") != "completed"
            or part.get("tool") not in ("bash", "shell")
        ):
            continue
        inputs, metadata = state.get("input"), state.get("metadata")
        if not isinstance(inputs, dict) or not isinstance(metadata, dict):
            continue
        command = inputs.get("command")
        code = metadata.get("exit")
        if "exit" not in metadata and isinstance(metadata.get("metadata"), dict):
            code = metadata["metadata"].get("exit")
        if not isinstance(command, str) or type(code) is not int:
            continue
        identity = (row.get("sessionID"), part.get("messageID"), command, code)
        previous = identities.setdefault(key, identity)
        if previous != identity:
            ambiguous.add(key)
    return ambiguous
