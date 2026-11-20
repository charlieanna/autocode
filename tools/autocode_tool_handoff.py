"""Supply executable runner tools to provider handoffs, without controller imports."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

MARKER = "\nCURRENT HANDOFF DATA\n"


def before_handoff(prompt: str, text: str) -> str:
    """``prompt`` with ``text`` added to its instructions, ahead of the handoff packet.

    Every launch helper reads everything after ``MARKER`` as one JSON object, so text
    added to a built stage prompt (guidance, rules, notes) goes before the marker: appended
    after the packet it paused the stage before any provider launched (``PAUSED_INVALID_OUTPUT``,
    "Provider handoff must contain a JSON object"). A prompt with no handoff takes it at its end.
    """
    instructions, marker, packet = prompt.partition(MARKER)
    return instructions + text + marker + packet if marker else prompt + text


def capture_command(mode=None) -> str:
    """The public capture prefix shared by handoffs and model-free tool admission."""
    words = [sys.executable, str(Path(__file__).with_name("autocode.py")), "capture"]
    if mode is not None:
        words += ["--mode", mode]
    return shlex.join(words)


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
    data["capture_command"] = capture_command()
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


def report_check_command(spec_path: Path | str) -> str:
    """Public read-only CLI command for pre-validating a candidate JSON report via stdin."""
    words = [
        sys.executable,
        str(Path(__file__).with_name("autocode.py")),
        "report-check",
        "--spec",
        str(spec_path),
        "-",
    ]
    return shlex.join(words)


def with_report_check_command(prompt: str, spec_path: Path | str) -> str:
    """Inject a public pre-submit stdin validation command into runner-owned handoffs."""
    instructions, marker, packet = prompt.partition(MARKER)
    if not marker:
        return prompt
    try:
        data = json.loads(packet)
    except ValueError as error:
        raise ValueError("Provider handoff must contain a JSON object") from error
    if not isinstance(data, dict):
        raise ValueError("Provider handoff must contain a JSON object")
    if "report_check_command" in data:
        return prompt
    data["report_check_command"] = report_check_command(spec_path)
    # The one place every stage learns how to feed report-check (#3875). A pipe needs no file, so it
    # runs in any read-only shell; a here-document or here-string can need a temporary file the
    # stage's shell cannot create (docs/bugs/read-only-shell-input.md).
    note = (
        "\nBefore emitting your final JSON report, if shell access is enabled for this stage, "
        "you may validate candidate JSON via stdin (writing no workspace files) with "
        "`report_check_command` from CURRENT HANDOFF DATA. Pipe the JSON in, as "
        "printf '%s' '<candidate JSON>' | <report_check_command>, writing each ' inside the JSON "
        "as '\\''. Do not use a here-document (<<) or here-string (<<<): the shell backs them with "
        "a temporary file, which a read-only stage's shell may be unable to create.\n"
    )
    if not instructions.endswith("\n"):
        note = "\n" + note
    updated_instructions = instructions + note
    if "\n" not in packet.strip():
        return updated_instructions + marker + json.dumps(data, separators=(",", ":"))
    return updated_instructions + marker + json.dumps(data, indent=2)


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
