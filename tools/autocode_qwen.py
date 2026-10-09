"""Qwen Code transport. Uses Qwen CLI directly without OpenCode."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

DEFAULT_MODELS = {
    # Qwen model mapping for each role
    # Using qwen-max for planning/review roles (strong reasoning)
    # Using qwen-coder-plus for builder role (code generation)
    "astra": "qwen/qwen-max",
    "terra": "qwen/qwen-coder-plus",
    "sol": "qwen/qwen-max",
    "completion": "qwen/qwen-max",
}

DEFAULT_REASONING_EFFORTS = {
    "astra": "high",
    "terra": "medium",
    "sol": "high",
    "completion": "medium",
}


def local_settings(workspace):
    """Capture Qwen configuration for transport drift detection."""
    executable = shutil.which("qwen")
    if not executable:
        raise RuntimeError("Qwen is not on PATH")
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Qwen version check timed out; no agent was launched") from error
    version = result.stdout.strip()
    # Qwen version format may vary; accept any non-empty version string
    if result.returncode or not version:
        raise RuntimeError("Cannot determine Qwen version; inspect qwen --version")
    return {"engine": "qwen", "executable": executable, "version": version}


def transport_drift(current, checkpoint):
    """Check if Qwen configuration has changed since checkpoint."""
    for key in ("engine", "executable", "version"):
        if current.get(key) != checkpoint.get(key):
            return True
    return False


def check_models(roles, workspace=None):
    """Verify Qwen models are available."""
    # Qwen doesn't have a built-in model listing command
    # We'll validate model names by checking they follow the expected format
    for role, config in roles.items():
        model = config.get("model", "")
        if not model or "/" not in model:
            raise RuntimeError(f"Qwen model must use provider/model format, got: {model}")
        provider, model_name = model.split("/", 1)
        if provider != "qwen":
            raise RuntimeError(f"Qwen engine only supports 'qwen' provider, got: {provider}")


def launch(role, workspace, run_dir, session, model, effort, allow_write, *, planning=False):
    """Launch Qwen CLI with appropriate arguments.
    
    Returns: (command, env, overrides)
    """
    if not model or "/" not in model or any(c.isspace() for c in model):
        raise ValueError("Qwen model must use provider/model format, e.g. qwen/qwen-max")

    provider, model_name = model.split("/", 1)

    # Build Qwen CLI command
    # Qwen uses: qwen [options] [prompt]
    # Note: Qwen runs in the current directory, so we'll cd to workspace before launching
    command = ["qwen"]

    # Add model specification
    command.extend(["--model", model_name])

    # Add output format (JSON for structured output)
    command.extend(["--output-format", "json"])

    # Add session tracking if resuming
    if session:
        command.extend(["--resume", session])

    # Note: Qwen doesn't have a --reasoning-effort flag
    # Note: Qwen doesn't have a --dir flag, it uses cwd

    # For planning roles, we'll use approval-mode to restrict operations
    if planning or not allow_write:
        command.extend(["--approval-mode", "plan"])
    else:
        # For builder roles, use auto mode to allow file operations
        command.extend(["--approval-mode", "auto"])

    # Environment variables (pass through current environment)
    env = dict(os.environ)

    # No overrides needed for Qwen (unlike OpenCode's agent configuration)
    overrides = {}

    return command, env, overrides


def prompt_for_schema(prompt, schema, events):
    """Add schema instructions to the prompt for Qwen."""
    instructions = ("\nQWEN OUTPUT CONTRACT\n"
        "Return your final report as exactly one JSON object matching the following schema. "
        "Do not wrap it in explanation or markdown code blocks. "
        "The runner validates every required field.\n"
        "Your raw stage events are at " + str(events) + ". A command event may be cited only when "
        "it has completed with an exit code. "
        "Cite event:<event_id>, copy the command exactly, and report that exit code. "
        "Never invent event IDs or exit codes.\n"
        "An evidence_refs entry must contain only a file path or the exact event:<event_id>; "
        "never append a command, exit code, punctuation or explanation to a reference. "
        "Terra should prefer the saved capture JSON/log file paths in evidence_refs, "
        "and list commands separately in commands_run.\n"
        "Treat the workspace in CURRENT HANDOFF DATA as a strict filesystem boundary. Do not "
        "read, list, search, or modify parent directories, sibling projects, or external "
        "configuration files.\n"
        + json.dumps(schema, indent=2) + "\n")
    return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)


def raw_events(path):
    """Read raw JSON lines from Qwen output file."""
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def normalized_events(rows):
    """Adapt Qwen transport events to autocode's normalized format.
    
    Qwen's JSON output format includes:
    - Tool use events (bash/shell commands)
    - Text output
    - Final JSON report
    """
    if not rows:
        return [{"type": "turn.failed", "error": "No Qwen events found"}]

    # Extract session/thread ID if present
    session_id = None
    for row in rows:
        if "session_id" in row or "sessionID" in row:
            session_id = row.get("session_id") or row.get("sessionID")
            break

    if not session_id:
        session_id = "qwen-session-" + uuid.uuid4().hex[:8]

    normalized = [{"type": "thread.started", "thread_id": session_id}]

    # Process each event
    errors = []
    commands = []
    text_parts = []
    usage = {}
    turn_completed = False

    for row in rows:
        event_type = row.get("type", "")

        # Handle tool use (bash/shell commands)
        if event_type in ("tool_use", "tool_call") and row.get("tool") in ("bash", "shell", "run_shell_command"):
            state = row.get("state", {})
            command = state.get("input", {}).get("command") or row.get("input", {}).get("command")
            exit_code = state.get("metadata", {}).get("exit") or row.get("exit_code")
            output = state.get("output", "") or row.get("output", "")

            if command and isinstance(command, str) and isinstance(exit_code, int):
                normalized.append({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": row.get("id", uuid.uuid4().hex),
                        "command": command,
                        "exit_code": exit_code,
                        "aggregated_output": output if isinstance(output, str) else json.dumps(output)
                    }
                })

        # Handle errors
        elif event_type == "error" or row.get("error"):
            error_msg = row.get("error", "Unknown error")
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", str(error_msg))
            errors.append({"type": "error", "error": error_msg})

        # Handle text output
        elif event_type == "text" or "text" in row:
            text = row.get("text", "")
            if text:
                text_parts.append(text)

        # Handle turn completion
        elif event_type in ("turn.completed", "completed", "done"):
            turn_completed = True
            # Extract usage if present
            if "usage" in row:
                usage = row["usage"]

        # Handle turn failure
        elif event_type in ("turn.failed", "failed"):
            error_msg = row.get("error", "Turn failed")
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", str(error_msg))
            normalized.append({"type": "turn.failed", "error": error_msg})

    # Add any errors
    normalized.extend(errors)

    # Determine if turn completed successfully
    if not any(row.get("type") == "turn.failed" for row in normalized):
        if turn_completed or text_parts:
            normalized.append({"type": "turn.completed", "usage": usage if usage else None})

    return normalized


def final_report(path):
    """Extract and validate the final JSON report from Qwen output."""
    rows = raw_events(path)
    if not rows:
        raise RuntimeError("Qwen output file is empty; no report found")

    # Look for the final JSON report in the output
    # Qwen may output it as a text event or as the final message

    # First, try to find a JSON object in the text parts
    text_parts = []
    for row in rows:
        if "text" in row:
            text_parts.append(row["text"])
        elif row.get("type") == "text":
            text_parts.append(row.get("text", ""))

    # Combine all text parts
    full_text = "\n".join(text_parts).strip()

    # Try to parse the entire text as JSON
    report = None
    try:
        report = json.loads(full_text)
    except ValueError:
        # Try to find JSON in markdown code blocks
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.S)):
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue

        # Try to find any JSON object in the text
        if report is None:
            for match in reversed(re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", full_text)):
                try:
                    report = json.loads(match)
                    break
                except ValueError:
                    continue

    if not isinstance(report, dict):
        raise RuntimeError("Qwen final message is not a JSON report; inspect the saved output")

    return report
