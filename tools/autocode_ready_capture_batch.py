"""An executable presentation of plain approved checks, using the existing capture CLI."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

# Capture inherits the stage's source/attempt context and verification-copy environment.
# Every inner CLI prints its complete receipt and attestation; the outer tool retains both.
SCRIPT = """import json,subprocess,sys,uuid
plan=json.loads(sys.argv[1])
prefix=plan['directory']+'/ready-'+uuid.uuid4().hex
codes=[]
for index,command in enumerate(plan['commands'],1):
    receipt=prefix+'-'+str(index)+'.json'
    result=subprocess.run(plan['capture']+['--output',receipt,'--']+command)
    codes.append(result.returncode)
    print(json.dumps({'command':command,'exit_code':result.returncode,'evidence_ref':receipt}),flush=True)
sys.exit(1 if any(codes) else 0)
"""
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


def context(capture, commands, evidence_directory):
    """All checks in order, or no affordance; individual execution remains available."""
    if os.name != "posix" or not isinstance(commands, list) or not 2 <= len(commands) <= 16:
        return None
    prepared = [argv(command) for command in commands]
    if any(command is None for command in prepared):
        return None
    launcher = shlex.split(capture)
    payload = {"capture": launcher, "commands": prepared, "directory": str(Path(evidence_directory))}
    return {
        "command": shlex.join([launcher[0], "-c", SCRIPT, json.dumps(payload, separators=(",", ":"))]),
        "instruction": (
            "When ready, execute this batch once as one unfiltered tool call. It runs every exact approved "
            "required_command in sequential order through capture, including after a failed check, and "
            "prints a separate fresh receipt and check reference for each. Its aggregate exit is failure "
            "if any check fails. Report each individual command/result/receipt; the batch is not a check "
            "and grants no verification authority. Keep the complete output. Additional probes and the "
            "individual capture_command remain available. If your shell tool cannot report its outer "
            "exit code, use individual captures instead; a batch does not loosen receipt authentication."
        ),
    }
