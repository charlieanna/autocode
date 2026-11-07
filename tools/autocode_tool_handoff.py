"""Supply executable runner tools to provider handoffs, without controller imports."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

MARKER = "\nCURRENT HANDOFF DATA\n"


def with_capture_command(prompt: str) -> str:
    """Fill missing tool context; preserve an existing command and packet bytes.

    Specialized jobs construct their own handoffs rather than the build-stage
    context. Providers that require capture receipts must supply the same public
    CLI there. Its default mode comes from the runner's AUTOCODE_OUTPUT_MODE.
    A markerless prompt retains the provider's existing no-contract behavior.
    """
    instructions, marker, packet = prompt.partition(MARKER)
    if not marker:
        return prompt
    try:
        data = json.loads(packet)
    except ValueError as error:
        raise ValueError("Provider handoff must contain a JSON object") from error
    if not isinstance(data, dict):
        raise ValueError("Provider handoff must contain a JSON object")
    if "capture_command" in data:
        if not isinstance(data["capture_command"], str) or not data["capture_command"].strip():
            raise ValueError("Provider handoff capture_command must be a nonempty command")
        return prompt
    data["capture_command"] = shlex.join([sys.executable, str(Path(__file__).with_name("autocode.py")), "capture"])
    # Keep a compact packet compact: re-indenting a report repair's handoff pushed it over its cap (#1337).
    if "\n" not in packet.strip():
        return instructions + marker + json.dumps(data, separators=(",", ":"))
    return instructions + marker + json.dumps(data, indent=2)


def with_stage_events_log(prompt: str, events) -> str:
    """Carry the per-attempt events-log path in the handoff so the contract text stays static.

    The output contract names ``stage_events_log`` instead of embedding the path, keeping the
    contract prefix byte-identical across attempts of one stage for provider prefix reuse (#2113 A2).
    Never overwrites an existing key, and keeps a compact packet compact as ``with_capture_command`` does.
    """
    instructions, marker, packet = prompt.partition(MARKER)
    if not marker:
        return prompt
    try:
        data = json.loads(packet)
    except ValueError as error:
        raise ValueError("Provider handoff must contain a JSON object") from error
    if not isinstance(data, dict):
        raise ValueError("Provider handoff must contain a JSON object")
    if "stage_events_log" in data:
        return prompt
    data["stage_events_log"] = str(events)
    if "\n" not in packet.strip():
        return instructions + marker + json.dumps(data, separators=(",", ":"))
    return instructions + marker + json.dumps(data, indent=2)


def planning_packet(prompt: str) -> bool:
    """Whether a runner-owned handoff describes planning, for output instructions."""
    _, marker, packet = prompt.partition(MARKER)
    if not marker:
        return False
    try:
        data = json.loads(packet)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("joint_planning") is True
