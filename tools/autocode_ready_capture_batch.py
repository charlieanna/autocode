"""An executable presentation of plain approved checks, using the existing capture CLI."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

# The plan is data, never shell/Python source. Keep the tool call copyable without
# the nested shell quotes that failed before capture in the ISO-week trial.
MAX_PLAN_BYTES = 64 * 1024
MAX_ENCODED_BYTES = 4 * ((MAX_PLAN_BYTES + 2) // 3)


BUILTINS = frozenset(
    [
        ".",
        "!",
        ":",
        "alias",
        "bg",
        "break",
        "builtin",
        "case",
        "cd",
        "command",
        "continue",
        "coproc",
        "declare",
        "dirs",
        "disown",
        "do",
        "done",
        "elif",
        "else",
        "enable",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "fc",
        "fg",
        "fi",
        "for",
        "function",
        "hash",
        "help",
        "history",
        "if",
        "in",
        "jobs",
        "let",
        "local",
        "popd",
        "pushd",
        "read",
        "readonly",
        "return",
        "select",
        "set",
        "shift",
        "source",
        "then",
        "time",
        "trap",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "until",
        "wait",
        "while",
    ]
)


def argv(command):
    """Reject shell interpretation rather than changing an approved command's meaning."""
    if not isinstance(command, str) or not command or re.search(r"[$`*?\[\]~{}#\n\r]", command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return None
    if (
        not words
        or words[0] in BUILTINS
        or "=" in words[0]
        or any(word and all(char in ";&|<>()" for char in word) for word in words)
    ):
        return None
    return words


def _capture():
    """The running helper owns the launcher; copied data never selects it."""
    return [sys.executable, str(Path(__file__).resolve().with_name("autocode.py")), "capture"]


def _capture_mode(launcher):
    if not isinstance(launcher, list):
        return None
    if launcher == _capture():
        # Match capture's runtime default for previously offered bare launchers.
        mode = os.environ.get("AUTOCODE_OUTPUT_MODE", "conservative")
    elif launcher[:-2] == _capture() and launcher[-2:-1] == ["--mode"]:
        mode = launcher[-1]
    else:
        return None
    return mode if mode in ("raw", "conservative") else None


def context(capture, commands, evidence_directory):
    """All checks in order, or no affordance; individual execution remains available."""
    if os.name != "posix" or not isinstance(commands, list) or not 2 <= len(commands) <= 16:
        return None
    prepared = [argv(command) for command in commands]
    if any(command is None for command in prepared):
        return None
    if not isinstance(capture, str):
        return None
    try:
        mode = _capture_mode(shlex.split(capture))
    except ValueError:
        return None
    if mode is None:
        return None
    payload = {"mode": mode, "commands": prepared, "directory": str(Path(evidence_directory))}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) > MAX_PLAN_BYTES:
        return None
    encoded = base64.urlsafe_b64encode(raw).decode("ascii")
    return {
        "command": shlex.join([sys.executable, str(Path(__file__).resolve()), encoded]),
        "instruction": (
            "When ready, copy and execute this complete command once as one unfiltered tool call. "
            "Its encoded plan is opaque data; do not decode, rewrite or re-quote it. It runs every exact approved "
            "required_command in sequential order through capture, including after a failed check, and "
            "prints a separate fresh receipt and check reference for each. Its aggregate exit is failure "
            "if any check fails. Report each individual command/result/receipt; the batch is not a check "
            "and grants no verification authority. Native tools use their current writable scratch. "
            "Keep the complete output. Additional probes and the "
            "individual capture_command remain available. If your shell tool cannot report its outer "
            "exit code, use individual captures instead; a batch does not loosen receipt authentication."
        ),
    }


def _plan(encoded):
    """Reject malformed or unbounded input before any capture is launched."""
    if len(encoded) > MAX_ENCODED_BYTES:
        raise ValueError("Prepared capture batch plan is too large")
    try:
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("Prepared capture batch plan is too large")
        plan = json.loads(raw)
    except (binascii.Error, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid prepared capture batch plan") from error
    if (
        not isinstance(plan, dict)
        or set(plan) not in ({"mode", "commands", "directory"}, {"capture", "commands", "directory"})
        or not isinstance(plan["directory"], str)
        or "\0" in plan["directory"]
        or not isinstance(plan["commands"], list)
        or not 2 <= len(plan["commands"]) <= 16
        or not all(
            isinstance(command, list)
            and command
            and all(isinstance(word, str) and "\0" not in word for word in command)
            and command[0]
            for command in plan["commands"]
        )
    ):
        raise ValueError("Invalid prepared capture batch plan")
    mode = plan["mode"] if "mode" in plan else _capture_mode(plan["capture"])
    if mode not in ("raw", "conservative"):
        raise ValueError("Invalid prepared capture batch plan")
    return {"mode": mode, "commands": plan["commands"], "directory": plan["directory"]}


def run(encoded):
    """Use canonical capture for every check; this helper grants no authority."""
    plan = _plan(encoded)
    directory = Path(plan["directory"])
    control = os.environ.get("AUTOCODE_TOOL_CONTAINMENT_ID")
    if control is not None:
        scratch = Path(os.environ.get("TMPDIR", ""))
        manifest = os.environ.get("AUTOCODE_VERIFICATION_COPY")
        valid = re.fullmatch("tool-containment-[0-9a-f]{32}", control) is not None and scratch.is_absolute()
        expected = Path.cwd() / ".autocode" / control / "scratch"
        try:
            valid = valid and scratch == expected and scratch.resolve(strict=True) == scratch and scratch.is_dir()
            valid = valid and manifest == str(scratch.parent / "verification-copy.json")
            valid = valid and Path(manifest).is_file() and not Path(manifest).is_symlink()
            valid = valid and bool(os.environ.get("AUTOCODE_VERIFICATION_COPY_SHA256"))
        except OSError:
            valid = False
        if not valid:
            raise ValueError("Prepared capture batch requires the current native scratch directory")
        directory = scratch
    prefix = str(directory) + "/ready-" + uuid.uuid4().hex
    codes = []
    for index, command in enumerate(plan["commands"], 1):
        receipt = prefix + "-" + str(index) + ".json"
        result = subprocess.run(_capture() + ["--mode", plan["mode"], "--output", receipt, "--"] + command)
        codes.append(result.returncode)
        print(json.dumps({"command": command, "exit_code": result.returncode, "evidence_ref": receipt}), flush=True)
    return 1 if any(codes) else 0


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Prepared capture batch requires one encoded plan")
        sys.exit(run(sys.argv[1]))
    except ValueError as error:
        sys.exit(str(error))
