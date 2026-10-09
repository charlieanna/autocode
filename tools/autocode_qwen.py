"""Qwen Code transport. Uses the Qwen CLI directly, without OpenCode.

Qwen Code prints Anthropic-style events: an ``assistant`` message carries
``tool_use`` blocks and a later ``user`` message carries the matching
``tool_result``. ``--output-format stream-json`` prints one JSON object per
line, which is what AutoCode's event log, idle watchdog and evidence matching
read; ``--output-format json`` prints a single JSON array on one line and is
not usable here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


# Role defaults. Every id must exist in the operator's own Qwen Code model
# registry (modelProviders in ~/.qwen/settings.json); "qwen/" is AutoCode's
# transport prefix and is stripped before the CLI sees it. The Planner and the
# Plan Reviewer must be different ids, or the run pauses with
# PAUSED_CROSS_MODEL before any agent is launched.
DEFAULT_MODELS = {
    "astra": "qwen/qwen3.8-max",
    "terra": "qwen/qwen3.7-plus",
    "sol": "qwen/qwen3.8-max",
    "completion": "qwen/qwen3.8-max",
    "glm": "qwen/qwen3.7-plus",
    "plan_reviewer": "qwen/qwen3.8-max",
}

# Qwen Code has no reasoning-effort flag, so these are recorded for the run and
# for the route ladders but never reach the CLI.
DEFAULT_REASONING_EFFORTS = {
    "astra": "high",
    "terra": "medium",
    "sol": "high",
    "completion": "medium",
    "glm": "medium",
    "plan_reviewer": "high",
}

SHELL_TOOLS = ("run_shell_command",)

# On Qwen Code 0.25.0 a failed run_shell_command result is a diagnostic
# envelope ending in "Exit Code: N", a successful one is the command's bare
# stdout with no envelope, and a call the approval policy refused is prose with
# no exit code at all. Only the envelope is an exit code; guessing one from a
# refusal would fabricate command evidence.
EXIT_CODE = re.compile(r"(?m)^Exit Code: (-?\d+)\s*$")


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
    """Verify Qwen models are addressed the way this transport launches them."""
    # Qwen has no model-listing command, so availability is the operator's
    # claim; the format is what this module can actually check.
    for role, config in roles.items():
        model = config.get("model", "")
        if not isinstance(model, str) or not model or any(char.isspace() for char in model):
            raise RuntimeError(f"Qwen model must use provider/model format, got: {model!r}")
        provider, _ = model.split("/", 1) if "/" in model else ("", model)
        if provider != "qwen":
            raise RuntimeError(f"Qwen engine only supports 'qwen' provider, got: {model}")


def _sandbox(sandbox, allow_write, planning):
    if sandbox:
        return sandbox
    return "workspace-write" if allow_write and not planning else "read-only"


def launch(role, workspace, run_dir, session, model, effort, allow_write, *, planning=False,
           report=None, schema=None, sandbox=None, **_unused):
    """Build one `qwen` invocation.

    The runner pipes the stage prompt to stdin and starts the process in
    `workspace`, so neither is a command argument. `report` is unused: this
    transport is an events transport, and the runner writes the report it reads
    back from `structured_result`.
    """
    if not isinstance(model, str) or "/" not in model or any(char.isspace() for char in model):
        raise ValueError("Qwen model must use provider/model format, e.g. qwen/qwen3.8-max")
    provider, model_name = model.split("/", 1)
    if provider != "qwen":
        raise ValueError(f"Qwen engine only supports the 'qwen' provider, got: {provider}")

    command = ["qwen", "--output-format", "stream-json", "--model", model_name]
    if schema:
        # Native structured output: Qwen registers a structured_output tool and
        # ends the session on the first call that validates against the schema.
        command += ["--json-schema", "@" + str(schema)]
    if _sandbox(sandbox, allow_write, planning) == "read-only":
        # Plan mode exposes no shell and no edit tool, so a read-only stage
        # cannot change the workspace; it still exposes structured_output.
        command += ["--approval-mode", "plan"]
    else:
        # Not "auto": its classifier refused a second authorized command as out
        # of scope, and a refusal arrives as a failed tool result, not as the
        # denial AutoCode reports as a blocker. Builders must run their checks
        # and judges must write receipts under .autocode/, so containment for
        # these stages is the runner's before/after workspace snapshot.
        command += ["--approval-mode", "yolo"]
    if session:
        command += ["--resume", session]
    return command, dict(os.environ), {}


def prompt_for_schema(prompt, schema, events):
    """Add the structured-output and evidence contract to a stage prompt.

    No capture_command is offered: this transport reports through events, and
    the runner sets AUTOCODE_CAPTURE_CONTEXT only for report_file providers, so
    a receipt written here would be rejected. Checks cite executed events.
    """
    instructions = ("\nQWEN OUTPUT CONTRACT\n"
        "Return your final report through the structured_output tool, exactly matching the schema "
        "below. Do not write it to a file and do not return it as prose; the runner reads the "
        "structured output and validates every required field.\n"
        "The session ends on the first structured_output call that validates. Finish every check, "
        "read and command you intend to cite BEFORE you call it: nothing after that call is seen.\n"
        "Your raw stage events are read-only evidence at " + str(events) + ". "
        "Never write, truncate, replace, delete, chmod or repair this event log, including via shell "
        "commands. It is a live transport stream, not an output destination. If it looks damaged, "
        "report that fact in your report without changing it.\n"
        "A command may be cited as evidence only when its run_shell_command call completed and its "
        "result carries an exit code. Cite event:<tool call id>, copy the command exactly as you ran "
        "it, and report that exit code; a Validator check instead says event: with no ID and "
        "exit_code null, and the runner attaches both, so do not read this log to look them up. "
        "Never invent event IDs or exit codes.\n"
        "A command the approval policy refused has no exit code and is not command evidence. Report "
        "it as a blocker instead of citing it.\n"
        "An evidence_refs entry must contain only a file path, the exact event:<tool call id>, or a "
        "Validator's check:<n>; never append a command, exit code, punctuation or explanation to a "
        "reference. Prefer saved file paths in evidence_refs, and list commands separately in "
        "commands_run. A PASS report must list at least one successful executed check.\n"
        "Shell commands start in the current workspace: prefer source-relative paths. For tools "
        "requiring absolute paths, copy workspace from CURRENT HANDOFF DATA verbatim and append the "
        "relative source path; never reconstruct it from a run name or an earlier session.\n"
        "Treat the workspace in CURRENT HANDOFF DATA as a strict filesystem boundary. Do not read, "
        "list, search, or modify parent directories, sibling projects, or external configuration "
        "files, including any ancestor AGENTS.md. If a required operation is denied, report a "
        "blocker; do not bypass the denial.\n"
        + json.dumps(schema, separators=(",", ":")) + "\n")
    return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)


def raw_events(path):
    """Read the JSON lines Qwen's stream-json output wrote."""
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
            # stderr shares this log, so plain-text provider errors land here
            # and are read by autocode_provider_error_lines, never as events.
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def looks_like_qwen(rows):
    """True when raw rows are Qwen events, so the runner adapts rather than trusts them."""
    return any(isinstance(row.get("session_id"), str)
               and row.get("type") in ("system", "assistant", "user", "result", "stream_event")
               for row in rows)


def _blocks(row):
    content = (row.get("message") or {}).get("content")
    return [block for block in (content or []) if isinstance(block, dict)]


def _text_of(body):
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return "".join(part.get("text", "") for part in body if isinstance(part, dict))
    return "" if body is None else json.dumps(body)


def _exit_code(body, is_error):
    """The integer exit a tool result proves, or None when it proves nothing."""
    if not is_error:
        return 0
    match = EXIT_CODE.search(body)
    return int(match.group(1)) if match else None


def _usage(result):
    """Token counts in AutoCode's convention, from the terminal result event.

    Qwen's input_tokens already include cache reads and its output_tokens
    already include reasoning, so neither is added again; OpenCode's exclude
    both, which is why its adapter sums them.
    """
    usage = (result or {}).get("usage") or {}
    totals = {}
    if type(usage.get("input_tokens")) is int:
        totals["input_tokens"] = usage["input_tokens"]
    if type(usage.get("cache_read_input_tokens")) is int:
        totals["cached_input_tokens"] = usage["cache_read_input_tokens"]
    if type(usage.get("output_tokens")) is int:
        totals["output_tokens"] = usage["output_tokens"]
    thoughts = [((stats or {}).get("tokens") or {}).get("thoughts")
                for stats in (((result or {}).get("stats") or {}).get("models") or {}).values()]
    known = [value for value in thoughts if type(value) is int and value >= 0]
    if known and len(known) == len(thoughts):
        totals["reasoning_output_tokens"] = sum(known)
    return totals or None


def normalized_events(rows):
    """Adapt Qwen's events to the shapes AutoCode's evidence and usage checks read.

    Never interpret a model's prose as tool proof: only a completed
    run_shell_command result becomes command evidence.
    """
    if not rows:
        return [{"type": "turn.failed", "error": {"message": "No Qwen events found"}}]
    sessions = {row["session_id"] for row in rows if isinstance(row.get("session_id"), str)}
    if len(sessions) != 1:
        return [{"type": "turn.failed", "error": {"message": "Missing or mixed Qwen sessions"}}]
    normalized = [{"type": "thread.started", "thread_id": next(iter(sessions))}]
    commands = {}
    for row in rows:
        kind = row.get("type")
        if kind == "assistant":
            for block in _blocks(row):
                if block.get("type") != "tool_use" or block.get("name") not in SHELL_TOOLS:
                    continue
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and isinstance(block.get("id"), str) and block["id"]:
                    commands[block["id"]] = command
        elif kind == "user":
            for block in _blocks(row):
                if block.get("type") != "tool_result":
                    continue
                call_id = block.get("tool_use_id")
                command = commands.get(call_id)
                if not isinstance(command, str) or not isinstance(call_id, str):
                    continue
                body = _text_of(block.get("content"))
                code = _exit_code(body, bool(block.get("is_error")))
                item = {"type": "command_execution" if type(code) is int else "tool_output",
                        "id": call_id, "command": command, "aggregated_output": body}
                if type(code) is int:
                    item["exit_code"] = code
                normalized.append({"type": "item.completed", "item": item})
    result = next((row for row in reversed(rows) if row.get("type") == "result"), None)
    for denial in (result or {}).get("permission_denials") or []:
        normalized.append({"type": "error", "error": {"message": _text_of(denial)[:500] or "Qwen denied an operation"}})
    usage = _usage(result)
    if result is None:
        # The process ended without its terminal result: consumption survives,
        # but there is no proof the turn completed.
        normalized.append({"type": "usage.partial", "usage": usage})
        return normalized
    if result.get("is_error") or result.get("subtype") not in (None, "success"):
        # The provider's own words go out as the error, so a quota, a rate limit
        # or a content filter is classified from them and not from model prose.
        message = str(result.get("result") or result.get("error") or "").strip()
        normalized.append({"type": "turn.failed", "usage": usage, "error": {
            "message": (message or f"Qwen ended with subtype {result.get('subtype')!r}")[:500]}})
        return normalized
    normalized.append({"type": "turn.completed", "usage": usage})
    return normalized


def _trailing_report(final):
    """A reply of prose followed by exactly one complete JSON object, or None."""
    start = final.find("{")
    if start < 0 or "```" in final[:start]:
        return None
    try:
        report, end = json.JSONDecoder().raw_decode(final[start:].strip())
    except ValueError:
        return None
    if not isinstance(report, dict) or final[start:].strip()[end:].strip():
        return None
    return report


def final_report(path, *, recover_wrapped=False, response_path=None):
    """The stage report: Qwen's validated structured output, else its final text.

    `recover_wrapped` is accepted for parity with the OpenCode facade; the
    structured output is already schema-validated by the CLI, so there is no
    wrapped report to recover.
    """
    rows = raw_events(path)
    normalized = normalized_events(rows)
    if not any(row.get("type") == "turn.completed" for row in normalized):
        raise RuntimeError("Qwen turn has no successful terminal result; preserve it without automatic retry")
    result = next((row for row in reversed(rows) if row.get("type") == "result"), None) or {}
    value = result.get("structured_result")
    final = str(result.get("result") or "").strip()
    if response_path is not None:
        Path(response_path).write_text(final)
    if isinstance(value, dict):
        return value
    report = None
    try:
        report = json.loads(final)
    except ValueError:
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", final, re.S)):
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue
        if report is None:
            report = _trailing_report(final)
    if not isinstance(report, dict):
        raise RuntimeError("Qwen returned no structured report; inspect the saved raw events")
    return report
