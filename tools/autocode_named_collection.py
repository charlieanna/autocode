"""Read named collection output as a reported observation, never as executed proof.

This parser recognizes only pytest's quiet node-list format. Other runners and
ambiguous output remain unknown until their own output formats are supported.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_SUMMARY = re.compile(r"^(\d+) tests? collected in [0-9]+(?:\.[0-9]+)?s$")
_NODE = re.compile(r"^\S+::.+$")


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
    count = int(summary.group(1))
    names, extra = lines[:count], lines[count:-1]
    if (
        not names
        or len(names) != count
        or not all(_NODE.fullmatch(line) for line in names)
        or any("::" in line for line in extra)
    ):
        return None
    return {
        "kind": "reported_collection",
        "suite_command": shlex.join(words[:3]),
        "node_ids": names,
        "independently_verified": False,
    }


_PASSED = re.compile(r"^(\S+::\S+) PASSED\s+\[\s*\d+%\]$")
_EXECUTION_SUMMARY = re.compile(r"^=+\s*(\d+) passed in [0-9]+(?:\.[0-9]+)?s\s*=+$")
_COLLECTED = re.compile(r"^(?:collecting \.\.\. )?collected (\d+) items?$", re.IGNORECASE)


def pytest_execution(command, output):
    """Read a complete verbose passing execution as reported data, not as a proof."""
    return _pytest_execution(command, output, selected=False)


def pytest_selected_execution(command, output):
    """Read a scoped verbose pytest run for display only."""
    return _pytest_execution(command, output, selected=True)


def _pytest_execution(command, output, *, selected):
    if not isinstance(command, str) or not isinstance(output, str):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) < 4 or Path(words[0]).name not in {"python", "python3"}:
        return None
    if words[1:4] != ["-m", "pytest", "-v"] or bool(words[4:]) != selected:
        return None
    if any(not word or word.startswith("-") for word in words[4:]):
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or not (summary := _EXECUTION_SUMMARY.fullmatch(lines[-1])):
        return None
    headers = [match for line in lines if (match := _COLLECTED.fullmatch(line))]
    cases = [match.group(1) for line in lines if (match := _PASSED.fullmatch(line))]
    if len(headers) != 1 or len(cases) != int(headers[0].group(1)) or len(cases) != int(summary.group(1)):
        return None
    if (
        not cases
        or len(set(cases)) != len(cases)
        or any("::" in line and not _PASSED.fullmatch(line) for line in lines)
    ):
        return None
    return {
        "kind": "reported_execution",
        "suite_command": shlex.join(words[:3]),
        "node_ids": cases,
        "independently_verified": False,
    }


def pytest_junit_identities(collection, source_paths):
    """Map reported nodes to pytest's JUnit spelling using exact known source paths.

    This maps names only; it never says the collector or an execution is trusted.
    Any unsupported node, missing source or identity collision leaves the mapping unknown.
    """
    if not isinstance(collection, dict) or not isinstance(source_paths, (set, list, tuple, dict)):
        return None
    nodes = collection.get("node_ids")
    if not isinstance(nodes, list) or not nodes:
        return None
    try:
        paths = set(source_paths)
    except TypeError:
        return None
    owners: dict[str, set[str]] = {}
    for name in paths:
        if not isinstance(name, str):
            return None
        module = name.replace("/", ".")
        if module.endswith(".py"):
            module = module[:-3]
        owners.setdefault(module, set()).add(name)
    result = []
    for node in nodes:
        if not isinstance(node, str):
            return None
        pieces = node.split("::")
        path = pieces[0]
        identity = source_paths.get(path) if isinstance(source_paths, dict) else None
        if (
            len(pieces) < 2
            or not all(pieces)
            or path not in paths
            or (
                isinstance(source_paths, dict)
                and (not isinstance(identity, str) or not re.fullmatch(r"(?:executable:)?[0-9a-f]{64}", identity))
            )
            or path.startswith("/")
            or "\\" in path
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            return None
        module = path.replace("/", ".")
        if module.endswith(".py"):
            module = module[:-3]
        classname = ".".join([module, *pieces[1:-1]])
        parts = classname.split(".")
        possible = set().union(*(owners.get(".".join(parts[:i]), set()) for i in range(1, len(parts) + 1)))
        if possible != {path}:
            return None
        result.append(classname + "::" + pieces[-1])
    return result if len(result) == len(set(result)) else None
