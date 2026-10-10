"""Exact observations of an existing native debug-agent launch, never readiness.

No native process, provider request or model call is made by this module. The
caller supplies the final actual debug-agent response; expected configuration
cannot stand in for that response. Locator envelopes are separately bound by
their owned manifest hashes and are excluded from the environment digest to
avoid a self-referential digest of conformance's own locator.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
from copy import deepcopy
from pathlib import Path

ENVELOPES = ("AUTOCODE_TOOL_CONTAINMENT", "AUTOCODE_NATIVE_ROLE_ISOLATION")
FIELDS = {
    "version",
    "effective_agent",
    "effective_tools",
    "effective_permission",
    "final_inline_sha256",
    "executable",
    "executable_sha256",
    "command_sha256",
    "environment_sha256",
    "environment_excluded",
    "model",
    "reasoning_effort",
}


def _require(value, reason):
    if not value:
        raise RuntimeError("Native launch conformance: " + reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _option(command, name, *, required=True):
    count = command.count(name)
    _require(count == 1 if required else count <= 1, "missing or repeated " + name)
    if not count:
        return None
    index = command.index(name)
    _require(
        index + 1 < len(command) and isinstance(command[index + 1], str) and command[index + 1],
        "missing value for " + name,
    )
    return command[index + 1]


def _inputs(command, environment):
    _require(
        isinstance(command, list)
        and len(command) >= 2
        and command[1] == "run"
        and all(isinstance(value, str) and value for value in command),
        "invalid target command",
    )
    _require(
        isinstance(environment, dict)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()),
        "invalid target environment",
    )
    inline = environment.get("OPENCODE_CONFIG_CONTENT")
    _require(isinstance(inline, str) and inline, "missing raw final inline configuration")
    target = shutil.which(command[0], path=environment.get("PATH", ""))
    _require(target, "target executable is unavailable")
    executable = Path(target).resolve()
    _require(executable.is_absolute() and stat.S_ISREG(executable.stat().st_mode), "nonregular executable")
    return {
        "effective_agent": _option(command, "--agent"),
        "model": _option(command, "--model"),
        "reasoning_effort": _option(command, "--variant", required=False),
        "final_inline_sha256": _sha(inline.encode()),
        "executable": str(executable),
        "executable_sha256": _sha(executable.read_bytes()),
        "command_sha256": _digest(command),
        "environment_sha256": _digest({key: value for key, value in environment.items() if key not in ENVELOPES}),
        "environment_excluded": list(ENVELOPES),
    }


def observe(command, environment, executable, actual):
    """Retain only actual native tool/policy/name fields and exact launch inputs."""
    expected = _inputs(command, environment)
    _require(Path(executable).resolve() == Path(expected["executable"]), "debug executable differs from target")
    _require(
        isinstance(actual, dict) and actual.get("name") == expected["effective_agent"],
        "actual native agent name differs or is missing",
    )
    tools = actual.get("tools")
    _require(
        isinstance(tools, dict)
        and tools
        and all(isinstance(name, str) and type(value) is bool for name, value in tools.items())
        and tools.get("bash") is True
        and all(value is False for name, value in tools.items() if name != "bash"),
        "actual native tools are not exclusively Bash",
    )
    rules = actual.get("permission")
    _require(
        isinstance(rules, list)
        and rules
        and all(
            isinstance(rule, dict)
            and set(rule) == {"permission", "pattern", "action"}
            and all(isinstance(rule[key], str) for key in rule)
            and rule["action"] in ("allow", "deny", "ask")
            for rule in rules
        ),
        "actual native ordered permission rules are unsupported",
    )
    return {"version": 1, **expected, "effective_tools": deepcopy(tools), "effective_permission": deepcopy(rules)}


def verify(record, command, environment):
    """Rehash the exact current target; a stored observation alone is not authority."""
    _require(
        isinstance(record, dict)
        and set(record) == FIELDS
        and type(record.get("version")) is int
        and record["version"] == 1,
        "missing or unsupported actual launch identity",
    )
    for name in ("final_inline_sha256", "executable_sha256", "command_sha256", "environment_sha256"):
        _require(isinstance(record[name], str) and re.fullmatch("[a-f0-9]{64}", record[name]), "invalid " + name)
    expected = _inputs(command, environment)
    _require(all(record[name] == value for name, value in expected.items()), "current launch inputs changed")
    # Reuse the strict observed-field schema, never derive native tools or policy
    # from desired inline JSON. The byte-pinned conformance is the observation.
    observed = observe(
        command,
        environment,
        record["executable"],
        {
            "name": record["effective_agent"],
            "tools": record["effective_tools"],
            "permission": record["effective_permission"],
        },
    )
    _require(observed == record, "native observation schema changed")
    return record
