"""Read named collection output as a reported observation, never as executed proof.

This parser recognizes only pytest's quiet node-list format. Other runners and
ambiguous output remain unknown until their own output formats are supported.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_SUMMARY = re.compile(r"^(\d+) tests? collected in [0-9]+(?:\.[0-9]+)?s$")
_NODE = re.compile(r"^\S+::\S+$")


def pytest_collection(command, output):
    """Return named observations for one exact, unfiltered default-suite collection."""
    if not isinstance(command, str) or not isinstance(output, str):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) != 5 or Path(words[0]).name not in {"python", "python3"}:
        return None
    if words[1:3] != ["-m", "pytest"] or set(words[3:]) != {"--collect-only", "-q"}:
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or not (summary := _SUMMARY.fullmatch(lines[-1])):
        return None
    names = lines[:-1]
    if not names or len(names) != int(summary.group(1)) or not all(_NODE.fullmatch(line) for line in names):
        return None
    return {
        "kind": "reported_collection",
        "suite_command": shlex.join([*words[:3], "-q"]),
        "node_ids": names,
        "independently_verified": False,
    }
