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


DEFAULT_MODELS = {
    "astra": "openai/gpt-6-astra",
    "terra": "openai/gpt-5.6-terra",
    "sol": "zai-coding-plan/glm-5.3",
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


def launch(role, workspace, run_dir, session, model, effort, allow_write):
    if not model or "/" not in model or any(c.isspace() for c in model):
        raise ValueError("OpenCode model must use provider/model, e.g. zai-coding-plan/glm-5.3")
    agent = "autocode_" + role
    # Only restrict permissions here. Do not replace a user's denies/asks with
    # allows, and never enable OpenCode's --auto blanket permission approval.
    permissions = {"task": "deny", "question": "deny", "plan_enter": "deny", "plan_exit": "deny"}
    if not allow_write:
        permissions["edit"] = "deny"
    overrides = {"share": "disabled", "autoupdate": False,
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
        "Your raw stage events are at " + str(events) + ". For command evidence, read the "
        "tool_use rows where part.tool is bash and part.state.status is completed. "
        "Cite event:<part.id>, copy part.state.input.command exactly, and report "
        "part.state.metadata.exit. Never invent event IDs or exit codes.\n"
        "OpenCode tool permissions apply. Do not modify application code in Astra or Sol, "
        "including via shell commands or external tools. If a required operation is denied, "
        "report a blocker; do not bypass the permission.\n"
        "Treat the workspace in CURRENT HANDOFF DATA as a strict filesystem boundary. Do not "
        "read, list, search, or modify parent directories, sibling projects, or external "
        "configuration files, including any ancestor AGENTS.md.\n"
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


def normalized_events(rows):
    """Adapt real transport events; never interpret a model's prose as tool proof."""
    sessions = {r["sessionID"] for r in rows if isinstance(r.get("sessionID"), str)}
    if len(sessions) != 1:
        return [{"type": "turn.failed", "error": "Missing or mixed OpenCode sessions"}]
    session = next(iter(sessions))
    normalized = [{"type": "thread.started", "thread_id": session}]
    # Some versions repeat completed part updates; a part is still one operation.
    parts = {}
    errors = []
    for row in rows:
        if row.get("type") == "error":
            errors.append({"type": "error", "error": row.get("error")})
        part = row.get("part", {})
        if row.get("sessionID") == session and part.get("id"):
            parts[part["id"]] = row
    steps = []
    for row in parts.values():
        part = row["part"]
        if row.get("type") == "tool_use" and part.get("tool") == "bash":
            state = part.get("state", {})
            command = state.get("input", {}).get("command")
            code = state.get("metadata", {}).get("exit")
            if state.get("status") == "completed" and isinstance(command, str) and type(code) is int:
                normalized.append({"type": "item.completed", "item": {
                    "type": "command_execution", "id": part["id"], "command": command,
                    "exit_code": code, "aggregated_output": state.get("output", "")}})
        elif row.get("type") == "step_finish":
            steps.append(part)
    normalized += errors
    phases = [row for row in rows if row.get("type") in ("step_start", "step_finish")]
    if steps and phases[-1].get("type") == "step_finish" and steps[-1].get("reason") == "stop" and not errors:
        def total(field, subfield=None):
            values = [p.get("tokens", {}).get(field) for p in steps]
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
        normalized.append({"type": "turn.completed", "usage": {k: v for k, v in usage.items() if v is not None}})
    return normalized


def final_report(path):
    rows = raw_events(path)
    normalized = normalized_events(rows)
    if not any(row.get("type") == "turn.completed" for row in normalized):
        raise RuntimeError("OpenCode turn has no successful terminal step; preserve it without automatic retry")
    last_step = [row["part"] for row in rows if row.get("type") == "step_finish"][-1]
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
        # Models may wrap the report in commentary despite the schema instruction;
        # accept an explicitly fenced JSON block, but never a bare fragment in prose.
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", final, re.S)):
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue
    if not isinstance(report, dict):
        raise RuntimeError("OpenCode final message is not a JSON report; inspect the saved raw events")
    return report
