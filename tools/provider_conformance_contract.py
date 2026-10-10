"""Shared provider probe and evidence oracle; independent of workflow state."""

from __future__ import annotations

import json
import secrets
import subprocess
from pathlib import Path

try:
    from .autocode_support import same_command
    from .autocode_util import changed_paths, validate_schema
except ImportError:
    from autocode_support import same_command
    from autocode_util import changed_paths, validate_schema


COMMANDS = {"python3 probe.py success": 0, "python3 probe.py failure": 7}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["probe_id", "workspace", "nonce", "checks"],
    "properties": {
        "probe_id": {"type": "string"},
        "workspace": {"type": "string"},
        "nonce": {"type": "string"},
        "checks": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["command", "exit_code"],
                "properties": {"command": {"type": "string"}, "exit_code": {"type": "integer"}},
            },
        },
    },
}


def fixture(directory: Path) -> tuple[Path, str]:
    """An isolated Git workspace whose path cannot safely be guessed or shortened."""
    workspace = directory / "workspace with spaces" / ("nested-project-" + secrets.token_hex(16))
    workspace.mkdir(parents=True)
    nonce = secrets.token_hex(16)
    (workspace / "input.txt").write_text(nonce + "\n")
    (workspace / "output.txt").write_text("not built yet\n")
    (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n")
    (workspace / "probe.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "if sys.argv[1] == 'failure':\n"
        "    print('EXPECTED_FAILURE'); sys.exit(7)\n"
        "assert Path('output.txt').read_bytes() == Path('input.txt').read_bytes()\n"
        "print('PROBE_OK')\n"
    )
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Conformance",
            "-c",
            "user.email=conformance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Provider probe fixture",
        ],
    ):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True, text=True)
    return workspace.resolve(), nonce


def handoff(workspace: Path, phase: str) -> dict:
    return {"probe_id": secrets.token_hex(16), "workspace": str(workspace), "phase": phase, "commands": list(COMMANDS)}


def prompt(data: dict) -> str:
    action = (
        "Copy input.txt into output.txt, byte for byte. Only output.txt may change."
        if data["phase"] == "build"
        else "Validate the existing output.txt. Do not change any file, including output.txt."
    )
    return (
        "This is a small provider conformance probe. Work only inside the exact workspace below. "
        "Use relative paths after selecting that workspace; never shorten or reconstruct its path. "
        "Do not read ancestor directories or configuration files. " + action + "\n"
        "Read input.txt to obtain the nonce (its contents without the final newline). "
        "Execute each listed command exactly once as a separate shell call from the workspace. "
        "The failure command deliberately exits 7: preserve that exit code, do not repair it or retry. "
        "Return one JSON object matching the schema, with the current probe_id, exact workspace, "
        "nonce and both executed checks. No prose or code fences. "
        "The runner binds commands to actual tool events; do not manufacture evidence. "
        "Do not edit .git or .autocode.\n"
        "REPORT SCHEMA\n" + json.dumps(SCHEMA) + "\nCURRENT HANDOFF DATA\n" + json.dumps(data)
    )


def assess(*, report, rows, data, nonce, before, after, output, expected_session=None):
    """Fail closed on report, terminal, session, workspace, evidence or usage mismatch.

    This tests adapter observability, not hostile-agent containment. Snapshots and
    logs have the same trust boundary as the runtime's provider process.
    """
    failures = []
    try:
        validate_schema(report, SCHEMA)
    except ValueError as error:
        failures.append("report_schema: " + str(error))
    valid_shape = not failures
    if valid_shape:
        for key, expected in (("probe_id", data["probe_id"]), ("workspace", data["workspace"]), ("nonce", nonce)):
            if report[key] != expected:
                failures.append("report_identity: " + key)

    starts = [r.get("thread_id") for r in rows if r.get("type") == "thread.started"]
    sessions = set(s for s in starts if isinstance(s, str))
    session = next(iter(sessions)) if len(sessions) == 1 else None
    if (
        not isinstance(session, str)
        or not session
        or len(starts) != 1
        or (expected_session and session != expected_session)
    ):
        failures.append("session_identity")
    completed = [r for r in rows if r.get("type") == "turn.completed"]
    phases = [r["type"] for r in rows if r.get("type") in ("turn.started", "turn.completed", "turn.failed")]
    if (
        len(completed) != 1
        or not phases
        or phases[-1] != "turn.completed"
        or any(r.get("type") in ("error", "turn.failed") for r in rows)
    ):
        failures.append("terminal_completion")
    usage = completed[0].get("usage", {}) if len(completed) == 1 else {}
    if not isinstance(usage, dict):
        usage = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            failures.append("usage_missing_or_invalid: " + key)
    # Reasoning is a subset of normalized output, never added a second time.
    if (
        type(usage.get("cached_input_tokens")) is int
        and type(usage.get("input_tokens")) is int
        and usage["cached_input_tokens"] > usage["input_tokens"]
    ):
        failures.append("usage_cache_exceeds_input")
    reasoning = usage.get("reasoning_output_tokens")
    if "reasoning_output_tokens" in usage and (
        type(reasoning) is not int
        or reasoning < 0
        or (type(usage.get("output_tokens")) is int and reasoning > usage["output_tokens"])
    ):
        failures.append("usage_reasoning_invalid")

    changed = changed_paths(before, after)
    allowed = ["output.txt"] if data["phase"] == "build" else []
    if changed != allowed or before["head"] != after["head"]:
        failures.append("workspace_changes")
    if output != (nonce + "\n").encode():
        failures.append("output_contents")

    evidence = []
    if valid_shape:
        reported = {c["command"]: c["exit_code"] for c in report["checks"]}
        if reported != COMMANDS:
            failures.append("reported_checks")
        items = [
            r["item"]
            for r in rows
            if r.get("type") == "item.completed" and r.get("item", {}).get("type") == "command_execution"
        ]
        for command, code in COMMANDS.items():
            matches = [i for i in items if isinstance(i.get("command"), str) and same_command(i["command"], command)]
            marker = "PROBE_OK" if code == 0 else "EXPECTED_FAILURE"
            if (
                len(matches) != 1
                or type(matches[0].get("exit_code")) is not int
                or matches[0]["exit_code"] != code
                or not matches[0].get("id")
                or not isinstance(matches[0].get("aggregated_output"), str)
                or marker not in matches[0]["aggregated_output"]
            ):
                failures.append("command_evidence: " + command)
            else:
                evidence.append({"command": command, "exit_code": code, "event_id": matches[0]["id"]})
    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "session": session,
        "usage": usage,
        "changed_paths": changed,
        "evidence": evidence,
    }
