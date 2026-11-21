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


_SHELL_SYNTAX = set(";&|<>`$()\n")
_CAPTURE_VALUES = {"--output", "--mode", "--known-output-sha256"}


def _plain_words(command):
    """The words of a command line that holds no shell syntax outside quotes, else None.

    An operator, redirection, substitution or expansion makes the line more than the one
    program its words name, so it is not read as one."""
    quote, escaped = "", False
    for character in command:
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character in _SHELL_SYNTAX:
            return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def capture_invocation(command):
    """``(output, argv)`` when a command line is exactly one run of AutoCode's capture CLI, else None.

    A Tester runs a noisy check as ``<python> .../autocode.py capture --output R -- <argv>``. The
    check it proves is ``argv`` with receipt ``R``; the invocation itself writes ``R`` and refuses
    to run again once it exists, so it is never the check (``receipts.mismatch``). ``--compare-base``
    runs a different comparison and is not read as one check."""
    words = _plain_words(command) if isinstance(command, str) else None
    if not words:
        return None
    if Path(words[0]).name == "autocode" and words[1:2] == ["capture"]:
        rest = words[2:]
    elif len(words) > 2 and Path(words[1]).name == "autocode.py" and words[2] == "capture":
        rest = words[3:]
    else:
        return None
    output = None
    while rest and rest[0] != "--" and rest[0].startswith("--"):
        name, equals, value = rest[0].partition("=")
        if name == "--no-compress" and not equals:
            rest = rest[1:]
            continue
        if name not in _CAPTURE_VALUES:
            return None
        if not equals:
            if len(rest) < 2:
                return None
            value, rest = rest[1], rest[2:]
        else:
            rest = rest[1:]
        if name == "--output":
            output = value
    argv = rest[1:] if rest[:1] == ["--"] else rest
    return (output, argv) if output and argv else None


def ambiguous_native_command_ids(text):
    """IDs with conflicting completed native commands; repeated updates are one part.

    Normalization keeps the final update for each part. Hydrating an omitted
    command must not let that hide conflicting execution facts under the same ID.
    """
    identities, messages, exits = {}, {}, {}
    ambiguous = set()
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "tool_use" or not isinstance(row.get("sessionID"), str):
            # The native normalizer ignores rows without a string session owner.
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
        inputs = state.get("input")
        command = inputs.get("command") if isinstance(inputs, dict) else None
        if not isinstance(command, str):
            continue
        # A completed command is an identity fact even before its exit arrives.
        identity = (row["sessionID"], command)
        if identities.setdefault(key, identity) != identity:
            ambiguous.add(key)
        message = part.get("messageID")
        # An absent message locator is not a conflicting identity fact. A later
        # update may fill it; two different supplied locators remain ambiguous.
        if message is not None and messages.setdefault(key, message) != message:
            ambiguous.add(key)
        metadata = state.get("metadata")
        if not isinstance(metadata, dict):
            continue
        code = metadata.get("exit")
        if "exit" not in metadata and isinstance(metadata.get("metadata"), dict):
            code = metadata["metadata"].get("exit")
        # Missing/non-integer exits grant no proof and may refine later. Two
        # different known exits for the same part remain conflicting facts.
        if type(code) is int and exits.setdefault(key, code) != code:
            ambiguous.add(key)
    return ambiguous
