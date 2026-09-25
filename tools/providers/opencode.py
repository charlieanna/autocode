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


DEFAULT_MODELS = {
    "requirements": "zai-coding-plan/glm-5.3",
    "glm": "zai-coding-plan/glm-5.3",
    # Role names are stable workflow identifiers, not fixed model names.  The
    # normal lead/reviewer starts on Sol and escalates to Astra only when the
    # task warrants it.
    "astra": "openai/gpt-5.6-sol",
    "terra": "openai/gpt-5.6-terra",
    "sol": "openai/gpt-5.6-sol",
    "completion": "openai/gpt-5.6-sol",
    "plan_reviewer": "cursor-acp/claude-opus-5-5-high",
}

DEFAULT_REASONING_EFFORTS = {
    "astra": "high",
    "terra": "medium",
    "sol": "high",
    "completion": "medium",
}


def configuration_inputs(workspace):
    """Enumerate local configuration definitions, never OpenCode's auth database.

    Directory contents matter as well as directory names. Generated dependencies,
    caches and sessions are deliberately excluded from the configuration identity.
    """
    root = Path(workspace).resolve()
    global_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode"
    directories = {global_root, Path.home() / ".opencode"}
    paths = set()
    for parent in (root, *root.parents):
        paths.update(parent / name for name in ("opencode.json", "opencode.jsonc"))
        directories.add(parent / ".opencode")
        if (parent / ".git").exists():
            break
    if os.environ.get("OPENCODE_CONFIG_DIR"):
        directories.add(Path(os.environ["OPENCODE_CONFIG_DIR"]).expanduser())
    if os.environ.get("OPENCODE_CONFIG"):
        paths.add(Path(os.environ["OPENCODE_CONFIG"]).expanduser())
    managed = os.environ.get("OPENCODE_TEST_MANAGED_CONFIG_DIR")
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


def local_settings(workspace):
    executable = shutil.which("opencode")
    if not executable:
        raise RuntimeError("OpenCode is not on PATH")
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode version check timed out; no agent was launched") from error
    version = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"1\.\d+\.\d+(?:[-+].*)?", version):
        raise RuntimeError("This adapter requires OpenCode 1.x; inspect opencode --version")
    # Fingerprint configuration, never credentials. OAuth token refreshes must not
    # invalidate a run, and the runner never opens OpenCode's auth.json.
    fingerprints = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                    for p in configuration_inputs(workspace)}
    inline = {key: hashlib.sha256(os.environ[key].encode()).hexdigest() if key in os.environ else None
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


def check_models(roles, workspace=None):
    try:
        result = subprocess.run(["opencode", "models"], cwd=workspace, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode model listing timed out; no agent was launched") from error
    if result.returncode:
        raise RuntimeError("Cannot list OpenCode models; check opencode models and opencode auth list")
    available = set(result.stdout.splitlines())
    missing = [entry["model"] for entry in roles.values() if entry["model"] not in available]
    if missing:
        raise RuntimeError("Models unavailable in OpenCode: " + ", ".join(sorted(set(missing))))


def check_subscription_routes(roles, workspace=None):
    """Check OpenCode's nonsecret CLI auth summary, never its credential file.

    OpenAI selections in the subscription workflow must use the existing OAuth
    connection. Unrecognized output is not permission to switch to API billing.
    Other providers retain their existing configured authentication.
    """
    if not any(config.get("model", "").startswith("openai/") for config in roles.values()):
        return
    if any(key in os.environ for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
        raise RuntimeError("OpenAI API-key or endpoint environment overrides are present; "
                           "subscription selection will not silently change billing routes")
    try:
        result = subprocess.run(["opencode", "auth", "list"], cwd=workspace,
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Cannot verify OpenCode's OpenAI OAuth connection; no provider request was launched") from error
    summary = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout + result.stderr)
    modes = re.findall(r"^\s*[●•]\s+OpenAI\s+(\S+)\s*$", summary, re.MULTILINE)
    if result.returncode or modes != ["oauth"]:
        raise RuntimeError("OpenCode OpenAI models require a ChatGPT OAuth connection. Use OpenCode /connect → "
                           "OpenAI → ChatGPT Plus/Pro; API-key fallback is disabled")


def launch(role, workspace, run_dir, session, model, effort, allow_write, *, planning=False,
           report=None, schema=None, prompt_file=None, sandbox=None):
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
    env = dict(os.environ)
    inherited = json.loads(env.get("OPENCODE_CONFIG_CONTENT", "{}"))
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
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(combined)
    command = ["opencode", "run", "--dir", str(workspace), "--format", "json", "--agent", agent,
               "--model", model, "--title", f"Autocode {role}: {run_dir}"]
    if session:
        command += ["--session", session]
    if effort:
        command += ["--variant", effort]
    return command, env, overrides


def prompt_for_schema(prompt, schema, events):
    instructions = ("\nOPENCODE OUTPUT CONTRACT\n"
        "Return your final report as exactly one JSON object matching the following schema. "
        "Do not wrap it in explanation. OpenCode's --format json emits transport events; "
        "it does not validate your report. The runner validates every required field.\n"
        "Your raw stage events are at " + str(events) + ". A command event may be cited only when "
        "part.tool is bash, part.state.status is completed, and part.state.metadata.exit is an integer. "
        "Cite event:<part.id>, copy part.state.input.command exactly, and report that exit code. "
        "Never invent event IDs or exit codes.\n"
        "Some OpenCode provider bridges expose completed shell output without an exit code. That output "
        "is not command evidence. For checks run through such a shell, use the capture_command in "
        "CURRENT HANDOFF DATA with --output .autocode/evidence/<unique-name>.json -- <command>, "
        "then cite that receipt path in checks and criterion evidence. Never create or edit a receipt manually.\n"
        "For a captured check, checks[].command must equal the shell-joined command array "
        "inside the receipt (for example, python3 -), and checks[].exit_code must equal "
        "the receipt exit_code. The outer capture invocation is not the check command. "
        "A PASS report must list at least one successful executed check.\n"
        "An evidence_refs entry must contain only a file path or the exact event:<part.id>; "
        "never append a command, exit code, punctuation or explanation to a reference. "
        "Do not cite a step_finish or text part ID. Terra should prefer the saved capture "
        "JSON/log file paths in evidence_refs, and list commands separately in commands_run.\n"
        "OpenCode tool permissions apply. Do not modify application code in Astra or Sol, "
        "including via shell commands or external tools. If a required operation is denied, "
        "report a blocker; do not bypass the permission.\n"
        "Treat the workspace in CURRENT HANDOFF DATA as a strict filesystem boundary. Do not "
        "read, list, search, or modify parent directories, sibling projects, or external "
        "configuration files, including any ancestor AGENTS.md. The only source-file exceptions "
        "are the exact read-only workspace cache records in CURRENT HANDOFF DATA under "
        "private_source_exceptions. Use their workspacePath only, preserve their canonicalPath, "
        "sourceId, and SHA-256 identity, and do not treat this as permission to access the "
        "external canonical location or any other external file.\n"
        + json.dumps(schema, indent=2) + "\n")
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
    # Use the same unique-part ordering for terminal evidence and usage. A
    # replayed older finish must not close a newer, still-unfinished step.
    phase_types = ("step_start", "step_finish")
    if any(not row.get("part", {}).get("id") for row in rows if row.get("type") in phase_types):
        return normalized
    phases = [row for row in parts.values() if row.get("type") in phase_types]
    if steps and phases[-1].get("type") == "step_finish" and steps[-1].get("reason") in ("stop", "length"):
        def total(field, subfield=None):
            containers = [p.get("tokens") for p in steps]
            values = [tokens.get(field) if isinstance(tokens, dict) else None for tokens in containers]
            if subfield:
                values = [v.get(subfield) if isinstance(v, dict) else None for v in values]
            return sum(values) if all(type(v) is int and v >= 0 for v in values) else None
        # OpenCode input excludes cache reads/writes. Preserve all input usage for
        # the runner's budget, including tokens served from a prompt cache.
        input_parts = [total("input"), total("cache", "read"), total("cache", "write")]
        output_parts = [total("output"), total("reasoning")]
        usage = {"input_tokens": sum(input_parts) if all(v is not None for v in input_parts) else None,
                 "cached_input_tokens": total("cache", "read"),
                 "output_tokens": sum(output_parts) if all(v is not None for v in output_parts) else None,
                 "reasoning_output_tokens": total("reasoning")}
        usage = {k: v for k, v in usage.items() if v is not None}
        if steps[-1].get("reason") == "length":
            # A successful process exit can still be an incomplete model turn.
            # Preserve reported consumption without granting completion evidence.
            normalized.append({"type": "turn.failed", "usage": usage, "error": {
                "code": "output_token_limit",
                "message": "OpenCode exhausted its output token limit (finish reason: length). "
                           "The attempt is incomplete; review saved work before recovery."}})
        elif not errors:
            normalized.append({"type": "turn.completed", "usage": usage})
    return normalized


def _repeated_repair_report(final):
    """Recover a report-only reply with brief prose and duplicate identical JSON."""
    start = final.find("{")
    if start < 0 or start > 500 or "```" in final[:start]:
        return None
    decoder = json.JSONDecoder()
    reports = []
    remaining = final[start:].strip()
    while remaining and len(reports) < 2:
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


def final_report(path, *, recover_wrapped=False):
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
        # accept an explicitly fenced JSON block, but never a bare fragment in prose.
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", final, re.S)):
            if report is not None:
                break
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue
        if report is None and recover_wrapped:
            report = _repeated_repair_report(final)
    if not isinstance(report, dict):
        raise RuntimeError("OpenCode final message is not a JSON report; inspect the saved raw events")
    return report
