"""OpenCode 1.x transport. Credentials stay in OpenCode's own provider setup."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

# os and shutil stay imported: the autocode_opencode compatibility shim star-exports
# them, and existing tests patch oc.os / oc.shutil to fake the process environment.

try:
    from . import env_prep
except ImportError:  # Script-style execution from tools/ remains supported.
    from providers import env_prep


try:
    from .. import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_tool_handoff as tool_handoff


DEFAULT_MODELS = {
    # Planning path (Z.ai): Requirements medium → Planner high.
    "requirements": "zai-coding-plan/glm-5.3",
    "glm": "zai-coding-plan/glm-5.3",
    # Independent Plan Reviewer must not be the Planner's model (or family).
    # GPT-6 Sol, not Astra: Astra is too expensive and only for the Resolver (user 2026-09-28).
    "plan_reviewer": "openai/gpt-6-sol",
    # Execution path (user 2026-09-29): the cheap model does the volume, the expensive one
    # judges it. Builder on GLM; Validator and Completion Owner check on GPT-6 Sol, so the
    # verifier never grades its own work (docs/models.md independence). A stuck Builder's
    # stronger attempt runs GPT-6 Sol and GLM checks it (autocode_builder_policy).
    "terra": "zai-coding-plan/glm-5.3",
    "sol": "openai/gpt-6-sol",
    "completion": "openai/gpt-6-sol",
    # The Resolver (astra role) is the only default use of GPT-6 Astra.
    "astra": "openai/gpt-6-astra",
}

# Ladder entry points (docs/models.md) — start medium where the ladder says so,
# escalate to higher reasoning inside the stage when evidence shows struggle.
DEFAULT_REASONING_EFFORTS = {
    "requirements": "medium",
    "glm": "high",
    "plan_reviewer": "high",
    "terra": "medium",
    "sol": "high",
    "completion": "medium",
    "astra": "high",
}


def _configuration_path(value, home):
    if value == "~" or value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()


def configuration_inputs(workspace, *, env=None):
    """Enumerate local configuration definitions, never OpenCode's auth database.

    Directory contents matter as well as directory names. Generated dependencies,
    caches and sessions are deliberately excluded from the configuration identity.
    Configuration roots (XDG_CONFIG_HOME, HOME, OPENCODE_CONFIG_DIR, OPENCODE_CONFIG,
    OPENCODE_TEST_MANAGED_CONFIG_DIR) resolve from the effective environment: the
    explicit ``env`` mapping, or one snapshot of the process environment.
    """
    effective = env_prep.snapshot_environment(env)
    root = Path(workspace).resolve()
    home = Path(effective["HOME"]) if effective.get("HOME") else Path.home()
    global_root = (Path(effective["XDG_CONFIG_HOME"]) if effective.get("XDG_CONFIG_HOME") else home / ".config") / "opencode"
    directories = {global_root, home / ".opencode"}
    paths = set()
    for parent in (root, *root.parents):
        paths.update(parent / name for name in ("opencode.json", "opencode.jsonc"))
        directories.add(parent / ".opencode")
        if (parent / ".git").exists():
            break
    if effective.get("OPENCODE_CONFIG_DIR"):
        directories.add(_configuration_path(effective["OPENCODE_CONFIG_DIR"], home))
    if effective.get("OPENCODE_CONFIG"):
        paths.add(_configuration_path(effective["OPENCODE_CONFIG"], home))
    managed = effective.get("OPENCODE_TEST_MANAGED_CONFIG_DIR")
    directories.add(Path(managed) if managed else Path(
        "/Library/Application Support/opencode" if sys.platform == "darwin" else "/etc/opencode"))
    for directory in directories:
        # Resolve relative environment paths exactly as `opencode --dir` does.
        directory = directory if directory.is_absolute() else root / directory
        paths.update(directory / name for name in ("config.json", "opencode.json", "opencode.jsonc"))
        for name in ("agent", "agents", "mode", "modes", "command", "commands", "plugin", "plugins", "tool", "tools"):
            folder = directory / name
            if folder.is_dir():
                paths.update(p for p in folder.rglob("*") if p.is_file()
                             and p.suffix in (".md", ".json", ".jsonc", ".js", ".ts", ".mjs", ".cjs")
                             and "node_modules" not in p.relative_to(folder).parts)
    return sorted({(p if p.is_absolute() else root / p).absolute() for p in paths}, key=str)


def local_settings(workspace, *, env=None):
    effective = env_prep.snapshot_environment(env)
    cwd = workspace if env is not None else None
    executable = env_prep.resolve_executable("opencode", effective, cwd=cwd,
                                             allow_default_path=env is None)
    if not executable:
        raise RuntimeError("OpenCode is not on PATH; no provider request was launched")
    try:
        result = env_prep.preflight_run([executable, "--version"], effective, cwd=cwd,
                                        capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode version check timed out; no agent was launched") from error
    except OSError as error:
        raise RuntimeError("OpenCode version check cannot start; no provider request was launched") from error
    version = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"1\.\d+\.\d+(?:[-+].*)?", version):
        raise RuntimeError("This adapter requires OpenCode 1.x; inspect opencode --version")
    # Fingerprint configuration, never credentials. OAuth token refreshes must not
    # invalidate a run, and the runner never opens OpenCode's auth.json.
    fingerprints = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                    for p in configuration_inputs(workspace, env=effective)}
    inline = {key: hashlib.sha256(effective[key].encode()).hexdigest() if key in effective else None
              for key in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION", "OPENCODE_CONFIG_DIR",
                          "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_PURE", "OPENCODE_TEST_MANAGED_CONFIG_DIR")}
    return {"engine": "opencode", "identity_version": 2, "executable": executable, "version": version,
            "config_hashes": fingerprints, "environment_config_hashes": inline}


def transport_drift(current, checkpoint):
    if checkpoint.get("identity_version", 1) >= 2:
        return current != checkpoint
    # Old checkpoints did not record every config source. Check every identity
    # field they *did* record before establishing the expanded baseline.
    for key in ("engine", "executable", "version"):
        if current.get(key) != checkpoint.get(key):
            return True
    return any(current.get(section, {}).get(key) != value
               for section in ("config_hashes", "environment_config_hashes")
               for key, value in checkpoint.get(section, {}).items())


def available_models(workspace=None, *, env=None):
    """The model IDs ``opencode models`` offers this login."""
    # Slow opencode installations can take well over 30s just to list models
    # (observed ~57s on a free cursor-acp plan with 250+ entries). The call is
    # read-only and infrequent; give it room rather than failing the run before
    # any agent is launched.
    effective = env_prep.snapshot_environment(env)
    if env is not None and not env_prep.resolve_executable("opencode", effective, cwd=workspace):
        # The explicit environment cannot find the provider: nothing was started.
        raise RuntimeError("OpenCode is not on PATH; no agent was launched")
    try:
        result = env_prep.preflight_run(["opencode", "models"], effective, cwd=workspace,
                                        capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode model listing timed out; no agent was launched") from error
    except OSError as error:
        # The roster subprocess could not even be spawned (issue #226's failure
        # class): report it as the honest prelaunch state, never FileNotFoundError.
        raise RuntimeError("OpenCode is not on PATH; no agent was launched") from error
    if result.returncode:
        raise RuntimeError("Cannot list OpenCode models; check opencode models and opencode auth list; "
                           "no agent was launched")
    return set(result.stdout.splitlines())


def check_models(roles, workspace=None, *, env=None):
    available = available_models(workspace, env=env)
    missing = [entry["model"] for entry in roles.values() if entry["model"] not in available]
    if missing:
        raise RuntimeError("Models unavailable in OpenCode: " + ", ".join(sorted(set(missing)))
                           + "; `autocode models` lists what your plans offer")


def check_subscription_routes(roles, workspace=None, *, env=None):
    """Validate a configured OpenAI connection, accepting OAuth or API routes.

    Explicit API credentials/endpoints belong to the selected transport. No
    credentials are inspected or copied and no authentication fallback is made.
    The historical function name remains for provider-interface compatibility.
    """
    if not any(config.get("model", "").startswith("openai/") for config in roles.values()):
        return
    if any(key in env_prep.combined_environment(env)
           for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
        return
    try:
        failed, modes = _openai_auth_modes(workspace, env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Cannot verify OpenCode's OpenAI connection; no provider request was launched") from error
    if failed or modes not in (["oauth"], ["api"]):
        raise RuntimeError("OpenCode OpenAI models require a configured OAuth or API connection; "
                           "use OpenCode /connect. No provider request was launched")


def openai_auth(workspace=None, *, env=None):
    """How OpenCode signs in to OpenAI: "oauth" (the ChatGPT login), another mode such as
    "api", "missing" when OpenAI is not connected, or None when the summary cannot be read.
    OAuth and API connections pass check_subscription_routes."""
    try:
        failed, modes = _openai_auth_modes(workspace, env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if failed or len(modes) > 1:
        return None
    return modes[0] if modes else "missing"


def _openai_auth_modes(workspace, env=None):
    # `opencode auth list` is a full CLI cold start (7-15 s observed inside
    # containers, worse under load). A single slow start must not read as a
    # missing OAuth connection, so a transport-level failure retries a couple
    # of times; a completed check is never retried -- its verdict stands.
    effective = env_prep.snapshot_environment(env)
    for attempt in range(3):
        try:
            result = env_prep.preflight_run(["opencode", "auth", "list"], effective, cwd=workspace,
                                            require_executable=env is not None,
                                            capture_output=True, text=True, timeout=15)
            break
        except (OSError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
    summary = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout + result.stderr)
    return result.returncode, re.findall(r"^\s*[●•]\s+OpenAI\s+(\S+)\s*$", summary, re.MULTILINE)


def launch(role, workspace, run_dir, session, model, effort, allow_write, *, planning=False,
           report=None, schema=None, prompt_file=None, sandbox=None, env=None):
    if not model or "/" not in model or any(c.isspace() for c in model):
        raise ValueError("OpenCode model must use provider/model, e.g. zai-coding-plan/glm-5.3")
    agent = "autocode_" + role
    if planning:
        # Fresh name prevents deep-merged configured role/tool allows from
        # surviving our read-tools-only policy.
        agent += "_plan_" + uuid.uuid4().hex
    # Only restrict permissions here. Do not replace a user's denies/asks with
    # allows, and never enable OpenCode's --auto blanket permission approval.
    permissions = {"task": "deny", "question": "deny", "plan_enter": "deny", "plan_exit": "deny"}
    if not allow_write:
        permissions["edit"] = "deny"
    if planning:
        # An explicit read-tools-only planning agent: no shell, delegation, MCP,
        # plugins' tools or edit escape hatch. The runner persists its report.
        permissions = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow", "list": "allow",
                       "edit": "deny", "bash": "deny", "task": "deny", "question": "deny", "external_directory": "deny"}
    overrides = {"$schema": "https://opencode.ai/config.json", "share": "disabled", "autoupdate": False,
                 "agent": {agent: {"description": f"Autocode {role} role", "mode": "primary",
                                    "permission": permissions}}}
    child = env_prep.child_environment(env)
    inherited = json.loads(child.get("OPENCODE_CONFIG_CONTENT", "{}"))
    if not isinstance(inherited, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT must be a JSON object")
    # Preserve inline provider configuration and unrelated agents. Our reserved
    # role definitions are constructed for this launch only, never saved globally.
    agents = inherited.get("agent", {})
    if not isinstance(agents, dict) or not isinstance(agents.get(agent, {}), dict):
        raise ValueError("OpenCode inline agent configuration must contain agent objects")
    prior = agents.get(agent, {})
    policy = prior.get("permission", {})
    if isinstance(policy, str) and policy in ("allow", "ask", "deny"):
        policy = {"*": policy}
    if not isinstance(policy, dict):
        raise ValueError("OpenCode agent permissions must be an action or an object")
    definition = {**prior, **overrides["agent"][agent], "permission": {**policy, **permissions}}
    combined = {**inherited, **overrides, "agent": {**agents, agent: definition}}
    child["OPENCODE_CONFIG_CONTENT"] = json.dumps(combined)
    command = ["opencode", "run", "--dir", str(workspace), "--format", "json", "--agent", agent,
               "--model", model, "--title", f"Autocode {role}: {run_dir}"]
    if session:
        command += ["--session", session]
    if effort:
        command += ["--variant", effort]
    return command, child, overrides


def prompt_for_schema(prompt, schema, events):
    prompt = tool_handoff.with_capture_command(prompt)
    instructions = ("\nOPENCODE OUTPUT CONTRACT\n"
        "Return your final report as exactly one JSON object matching the following schema. "
        "Do not wrap it in explanation. OpenCode's --format json emits transport events; "
        "it does not validate your report. The runner validates every required field.\n"
        "Return the JSON in your final assistant response; do not write it to a file. "
        "Only the runner saves the report.\n"
        "Your raw stage events are read-only evidence at " + str(events) + ". "
        "Never write, truncate, replace, delete, chmod or repair this event log, including via shell commands. "
        "It is a live transport stream, not an output destination. If it looks damaged, report that fact "
        "in your response without changing it. A command event may be cited only when "
        "part.tool is bash, part.state.status is completed, and part.state.metadata.exit is an integer. "
        "Cite event:<part.id>, copy part.state.input.command exactly, and report that exit code; a Validator "
        "check instead says event: with no ID and exit_code null, and the runner attaches both, so do not "
        "read this log to look them up. Never invent event IDs or exit codes.\n"
        "Some OpenCode provider bridges expose completed shell output without an exit code. That output "
        "is not command evidence. For checks run through such a shell, use the capture_command in "
        "CURRENT HANDOFF DATA with --output .autocode/evidence/<unique-name>.json -- <command>, "
        "then cite that receipt path in checks and criterion evidence. Never create or edit a receipt manually.\n"
        "For a captured check, checks[].command must equal the shell-joined command array "
        "inside the receipt (for example, python3 -), and checks[].exit_code must equal "
        "the receipt exit_code. The outer capture invocation is not the check command. "
        "A PASS report must list at least one successful executed check.\n"
        "An evidence_refs entry must contain only a file path, the exact event:<part.id>, or a Validator's check:<n>; "
        "never append a command, exit code, punctuation or explanation to a reference. "
        "Do not cite a step_finish or text part ID. The Builder should prefer the saved capture "
        "JSON/log file paths in evidence_refs, and list commands separately in commands_run.\n"
        "OpenCode tool permissions apply. Do not modify application code in the Plan Reviewer or Validator, "
        "including via shell commands or external tools. If a required operation is denied, "
        "report a blocker; do not bypass the permission.\n"
        "Shell commands start in the current workspace: prefer source-relative paths and omit workdir "
        "unless a check needs an existing workspace subdirectory. For tools requiring absolute paths, "
        "copy workspace from CURRENT HANDOFF DATA verbatim and append the relative source path. "
        "Never reconstruct it from a run name, evidence-directory name, title, or earlier session. "
        "Absolute paths inside the workspace are not inherently forbidden; guessed sibling paths are. "
        "If a path appears outside the workspace, correct the path rather than requesting wider permissions.\n"
        "Treat the workspace in CURRENT HANDOFF DATA as a strict filesystem boundary. Do not "
        "read, list, search, or modify parent directories, sibling projects, or external "
        "configuration files, including any ancestor AGENTS.md. The only source-file exceptions "
        "are the exact read-only workspace cache records in CURRENT HANDOFF DATA under "
        "private_source_exceptions. Use their workspacePath only, preserve their canonicalPath, "
        "sourceId, and SHA-256 identity, and do not treat this as permission to access the "
        "external canonical location or any other external file.\n"
        + json.dumps(schema, separators=(",", ":")) + "\n")
    return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)


def raw_events(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _session_parts(rows, session):
    # Repeated updates replace a part's value without moving it past newer parts.
    parts = {}
    for row in rows:
        part = row.get("part", {})
        if row.get("sessionID") == session and part.get("id"):
            parts[part["id"]] = row
    return parts


def normalized_events(rows):
    """Adapt real transport events; never interpret a model's prose as tool proof."""
    sessions = {r["sessionID"] for r in rows if isinstance(r.get("sessionID"), str)}
    if len(sessions) != 1:
        return [{"type": "turn.failed", "error": "Missing or mixed OpenCode sessions"}]
    session = next(iter(sessions))
    normalized = [{"type": "thread.started", "thread_id": session}]
    # Some versions repeat completed part updates; a part is still one operation.
    parts = _session_parts(rows, session)
    errors = [{"type": "error", "error": row.get("error")} for row in rows if row.get("type") == "error"]
    steps = []
    for row in parts.values():
        part = row["part"]
        if row.get("type") == "tool_use" and part.get("tool") in ("bash", "shell"):
            state = part.get("state", {})
            command = state.get("input", {}).get("command")
            code = state.get("metadata", {}).get("exit")
            if state.get("status") == "completed" and isinstance(command, str) and type(code) is int:
                normalized.append({"type": "item.completed", "item": {
                    "type": "command_execution", "id": part["id"], "command": command,
                    "exit_code": code, "aggregated_output": state.get("output", "")}})
            elif (state.get("status") == "completed"
                  and isinstance(command, str) and isinstance(state.get("output"), str)):
                normalized.append({"type": "item.completed", "item": {
                    "type": "tool_output", "id": part["id"], "command": command,
                    "aggregated_output": state["output"]}})
        elif row.get("type") == "step_finish":
            steps.append(part)
    normalized += errors
    if not steps:
        return normalized
    # Replayed finishes cannot close newer steps, even when the newer ID is missing.
    phase_types = ("step_start", "step_finish")
    phases = [row for row in parts.values() if row.get("type") in phase_types]
    terminal = (not any(not row.get("part", {}).get("id") for row in rows if row.get("type") in phase_types)
                and phases[-1].get("type") == "step_finish"
                and steps[-1].get("reason") in ("stop", "length", "tool-calls"))
    partial = not terminal or bool(errors and steps[-1].get("reason") == "stop")

    def total(field, subfield=None):
        containers = [p.get("tokens") for p in steps]
        values = [tokens.get(field) if isinstance(tokens, dict) else None for tokens in containers]
        if subfield:
            values = [v.get(subfield) if isinstance(v, dict) else None for v in values]
        known = [v for v in values if type(v) is int and v >= 0]
        return sum(known) if known and (partial or len(known) == len(values)) else None

    # OpenCode input excludes cache reads/writes; output excludes reasoning.
    input_parts = [total("input"), total("cache", "read"), total("cache", "write")]
    output_parts = [total("output"), total("reasoning")]
    usage = {"input_tokens": sum(input_parts) if all(v is not None for v in input_parts) else None,
             "cached_input_tokens": total("cache", "read"),
             "output_tokens": sum(output_parts) if all(v is not None for v in output_parts) else None,
             "reasoning_output_tokens": total("reasoning")}
    usage = {k: v for k, v in usage.items() if v is not None}
    if not terminal:
        # Completed-step consumption survives interruption, but is not terminal proof.
        normalized.append({"type": "usage.partial", "usage": usage})
        return normalized
    # A stream that ends on a "tool-calls" finish stopped mid-turn: the model asked
    # for tools and no later step followed (the process exited, for example after
    # every call was auto-rejected). Like "length", it is a failed turn whose
    # reported usage is retained for accounting, including cached input.
    if steps[-1].get("reason") == "length":
        # A successful process exit can still be an incomplete model turn.
        normalized.append({"type": "turn.failed", "usage": usage, "error": {
            "code": "output_token_limit",
            "message": "OpenCode exhausted its output token limit (finish reason: length). "
                       "The attempt is incomplete; review saved work before recovery."}})
    elif steps[-1].get("reason") == "tool-calls":
        normalized.append({"type": "turn.failed", "usage": usage, "error": {
            "code": "incomplete_turn",
            "message": "OpenCode stopped after a step that requested tool calls, before the model "
                       "finished its turn (finish reason: tool-calls). The attempt is incomplete; "
                       "review saved work before recovery."}})
    elif not errors:
        normalized.append({"type": "turn.completed", "usage": usage})
    else:
        normalized.append({"type": "usage.partial", "usage": usage})
    return normalized


def _trailing_report(final, *, copies=1):
    """Recover a reply of prose followed by the complete JSON report and nothing else.

    Models sometimes lead with a summary ("The probe confirms ... Report:") despite the
    JSON-only instruction, whatever its length (a live attempt led with 730 characters
    and was repaired at full cost). Accept any prose lead without a code fence as long
    as the message ENDS with one complete JSON object, so a fragment quoted inside an
    explanation or a second object is never taken for the report. Report-only repairs
    (``copies=2``) also accept the same object repeated twice. Every recovered report is
    still validated against the stage's schema by the runner."""
    start = final.find("{")
    if start < 0 or "```" in final[:start]:
        return None
    decoder = json.JSONDecoder()
    reports = []
    remaining = final[start:].strip()
    while remaining and len(reports) < copies:
        try:
            report, end = decoder.raw_decode(remaining)
        except ValueError:
            return None
        if not isinstance(report, dict):
            return None
        reports.append(report)
        remaining = remaining[end:].strip()
    if remaining or not reports or any(report != reports[0] for report in reports[1:]):
        return None
    return reports[0]


def final_report(path, *, recover_wrapped=False, response_path=None):
    rows = raw_events(path)
    normalized = normalized_events(rows)
    if not any(row.get("type") == "turn.completed" for row in normalized):
        raise RuntimeError("OpenCode turn has no successful terminal step; preserve it without automatic retry")
    parts = _session_parts(rows, normalized[0]["thread_id"])
    last_step = [row["part"] for row in parts.values() if row.get("type") == "step_finish"][-1]
    message = last_step.get("messageID")
    texts = {}
    for row in rows:
        part = row.get("part", {})
        if row.get("type") == "text" and message and part.get("messageID") == message:
            texts[part["id"]] = part.get("text", "")
    final = "\n".join(texts.values()).strip()
    if response_path is not None:
        # Retain only the completed assistant message, not tool output or earlier
        # turns, even when its JSON is malformed and parsing below must reject it.
        Path(response_path).write_text(final)
    report = None
    try:
        report = json.loads(final)
    except ValueError:
        # OpenCode can emit commentary and the final report as distinct text
        # parts under the same completed message. Accept the final complete JSON
        # part only when there is exactly one JSON-object part in that message.
        parts = [text.strip() for text in texts.values() if text.strip()]
        parsed_parts = []
        for index, part_text in enumerate(parts):
            try:
                candidate = json.loads(part_text)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                parsed_parts.append((index, candidate))
        if len(parsed_parts) == 1 and parsed_parts[0][0] == len(parts) - 1:
            report = parsed_parts[0][1]
        # Models may wrap the report in commentary despite the schema instruction;
        # accept an explicitly fenced JSON block, or brief prose followed by the complete
        # report at the very end, but never a fragment with commentary after it.
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", final, re.S)):
            if report is not None:
                break
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue
        if report is None:
            report = _trailing_report(final, copies=2 if recover_wrapped else 1)
    if not isinstance(report, dict):
        raise RuntimeError("OpenCode final message is not a JSON report; inspect the saved raw events")
    return report


def incomplete_response(path):
    """Return text from the terminal length-limited message, never a report.

    Callers must label this as incomplete and keep its events as the evidence
    source. It is useful only to help a bounded report repair preserve findings
    already emitted before the provider reached its output limit.
    """
    rows = raw_events(path)
    terminal = [row for row in rows if row.get('type') == 'step_finish'
                and row.get('part', {}).get('reason') == 'length']
    if not terminal:
        return None
    finish = terminal[-1]
    part = finish.get('part') or {}
    message_id = part.get('messageID')
    session_id = part.get('sessionID') or finish.get('sessionID')
    if not message_id:
        return None
    texts = {}
    for row in rows:
        body = row.get('part') or {}
        if (row.get('type') == 'text' and body.get('messageID') == message_id
                and (not session_id or (body.get('sessionID') or row.get('sessionID')) == session_id)
                and isinstance(body.get('text'), str)):
            part_id = body.get('id')
            key = str(part_id) if part_id is not None else str(len(texts))
            if key in texts and texts[key] != body['text']:
                return None
            texts[key] = body['text']
    text = '\n'.join(texts.values()).strip()
    return text if text and len(text.encode('utf-8')) <= 128 * 1024 else None
