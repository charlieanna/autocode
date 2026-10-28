"""Claude Code as a built-in provider: every stage is one ``claude -p`` call (#717).

``--engine claude`` (or ``--provider claude``) selects this facade on the OpenCode engine, as a
configured tool: bare ``claude-*`` model ids, no OpenCode containment. Each stage runs

    python -I providers/claude_stream.py --ledger RUN/claude-sessions --schema SCHEMA ... -- claude -p ...

with the stage prompt on standard input. The translator (``claude_stream``) writes Codex-shaped
events, so the stage's thread, usage, cost, command evidence and terminal row are read like a
Codex stage's (``OUTPUT = "codex_events"``). The report is the StructuredOutput input Claude Code
accepted, kept as the ``structured_output`` item of the event log.

- Sessions: every ordinary stage starts a fresh Claude session (``RESUME_ACROSS_STAGES = False``,
  honoured by ``autocode_provider_launch.prepare``); only a same-stage correction and the
  conformance resume phase pass ``--resume``.
- Tools: the same ``--tools Bash,Read,Edit,Write,Glob,Grep`` on every stage, no MCP servers, no
  skills, project settings only; Edit and Write are pre-approved unless the stage's sandbox is
  read-only; git history commands are denied.
- Login: the provider runs only on a Claude subscription login (``claude auth status --json`` in
  the agent's environment). An API key, Bedrock or Vertex never reaches Claude Code through AutoCode:
  not through the environment, not through the project's ``.claude/settings.json``, and a stage whose
  Claude Code reports an API key source anyway is stopped by the translator.
- Identity: the launch policy below (hashed), the resolved executable, ``claude --version``, the
  workspace's ``.claude/settings.json`` and two endpoint variables (hashed). A change pauses a saved
  run (``PAUSED_TRANSPORT_CHANGED``). Reviewer rule: a change to what the translator's rows mean
  bumps ``ADAPTER``; an edit that keeps their meaning does not.

Imports only lower-level helpers, never ``autocode_providers`` or anything above it.
"""

from __future__ import annotations

try:
    from .. import autocode_agent_env as agent_env
    from .. import autocode_prompts as prompts
    from .. import autocode_provider_env as provider_env
    from .. import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_prompts as prompts
    import autocode_provider_env as provider_env
    import autocode_tool_handoff as tool_handoff

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

try:
    from . import claude_stream, env_prep
except ImportError:  # Script-style execution from tools/.
    from providers import claude_stream, env_prep


CONFIGURED = True
NAME = "claude"
OUTPUT = "codex_events"
SUPPORTS_SESSIONS = True
RESUME_ACROSS_STAGES = False
PROMPT_MODE = "stdin"
ADAPTER = "claude-stream-1"
HAIKU, SONNET, OPUS = "claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"
LISTED_MODELS = [HAIKU, SONNET, OPUS]
# Verifiers differ from producers: Haiku builds, Sonnet drafts and tests, Opus reviews and completes.
DEFAULT_MODELS = {
    "requirements": SONNET,
    "glm": SONNET,
    "plan_reviewer": OPUS,
    "terra": HAIKU,
    "sol": SONNET,
    "completion": OPUS,
    "astra": OPUS,
}
DEFAULT_REASONING_EFFORTS = {role: "medium" for role in DEFAULT_MODELS}
BUILDER_RETRY = {"strong_model": SONNET, "checker_model": OPUS}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TOOLS = "Bash,Read,Edit,Write,Glob,Grep"
WRITE_ALLOWED = ("Read", "Grep", "Glob", "Bash", "Edit", "Write")
READ_ALLOWED = ("Read", "Grep", "Glob", "Bash")
READ_DISALLOWED = ("Edit", "Write", "NotebookEdit")
DENY_GIT = (
    "Bash(git commit:*)",
    "Bash(git push:*)",
    "Bash(git checkout:*)",
    "Bash(git reset:*)",
    "Bash(git stash:*)",
    "Bash(git branch:*)",
    "Bash(git rebase:*)",
    "Bash(git merge:*)",
)
BASE_FLAGS = (
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--setting-sources",
    "project",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--tools",
    TOOLS,
)
# Removed from every environment the facade spawns: verified live (2.1.299) to keep a nested
# subscription login working while the child stops inheriting the parent session's identity.
REMOVED_ENV = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_REMOTE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_DEBUG",
    "CLAUDE_CODE_DIAGNOSTICS_FILE",
    "CLAUDE_CODE_TEE_SDK_STDOUT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_EFFORT",
    "CLAUDE_AUTO_BACKGROUND_TASKS",
    "CLAUDE_CODE_ENTRYPOINT",
)
SET_ENV = {"DISABLE_AUTOUPDATER": "1"}
BILLING_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# The CLAUDE_CODE_USE_* names that route requests to another provider or account (Claude Code 2.1.299);
# the rest of that prefix (CCR_V2, NATIVE_FILE_SEARCH, POWERSHELL_TOOL, COWORK_PLUGINS) are features.
BILLING_ROUTES = tuple(
    "CLAUDE_CODE_USE_" + route
    for route in ("BEDROCK", "VERTEX", "FOUNDRY", "GATEWAY", "MANTLE", "ANTHROPIC_AWS", "ANTHROPIC_GOOGLE_CLOUD")
)
SUBSCRIPTION_AUTH = ("claude.ai", "oauth_token")
# What a project's .claude/settings.json may not set: credential helpers and the env entries that pick
# an API key, another provider, another account or another endpoint.
PROJECT_BILLING_SETTINGS = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh")
_PROJECT_BILLING_ENV = re.compile(
    r"ANTHROPIC_\w*(API_KEY|AUTH_TOKEN|BASE_URL)|" + "|".join(BILLING_ROUTES) + r"|CLAUDE_CODE_OAUTH_TOKEN\w*"
)
HASHED_ENV = ("ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR")
POLICY = {
    "base_flags": BASE_FLAGS,
    "write_allowed": WRITE_ALLOWED,
    "read_allowed": READ_ALLOWED,
    "read_disallowed": READ_DISALLOWED,
    "deny_git": DENY_GIT,
    "removed_env": REMOVED_ENV,
    "set_env": SET_ENV,
}
STREAM = Path(__file__).with_name("claude_stream.py")
# The exact literal the shared recovery modules match (autocode_report_retry, autocode_format_correction,
# autocode_stage_recovery): a stage that finished without a report gets the same-session format correction.
FORMAT_REJECTION = "OpenCode final message is not a JSON report; inspect the saved raw events"
_VERSION = re.compile(r"\s*(\d+\.\d+\.\d+)\b")
_FALSE = ("", "0", "false", "False")
_LOGIN = "run `claude auth login`"


def parse_version(text: str) -> str | None:
    """``2.1.299`` from ``2.1.299 (Claude Code)``; None for anything else."""
    match = _VERSION.match(text) if isinstance(text, str) else None
    return match.group(1) if match else None


NOT_ON_PATH = (
    "claude is not on PATH; no provider request was launched. Run `autocode doctor` to see what this host has: "
    "an installed Codex or OpenCode runs with --engine codex or --engine opencode"
)


def child_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment Claude Code runs in: the agent environment, minus the parent session's identity."""
    base = agent_env.scrubbed(env_prep.snapshot_environment()) if env is None else dict(env)
    child = {name: value for name, value in base.items() if name not in REMOVED_ENV}
    child.update(SET_ENV)
    return child


def check_forbidden_environment(env: Mapping[str, str] | None = None, *, workspace=None) -> None:
    """Refuse a billing route that would reach Claude Code: never one only in the user's own shell.

    With ``workspace``, also the project's ``.claude/settings.json``: every stage loads it
    (``--setting-sources project``), and ``-p`` trusts it without asking, while ``claude auth status``
    does not apply an untrusted project's settings, so the login check cannot see them."""
    effective = child_environment(agent_env.scrubbed(env_prep.snapshot_environment(env)))
    for name in BILLING_KEYS:
        if name in effective:
            raise RuntimeError(
                f"{name} reaches Claude Code; claude subscription selection will not silently change billing routes"
            )
    for name in BILLING_ROUTES:
        if effective.get(name, "") not in _FALSE:
            raise RuntimeError(
                f"{name} reaches Claude Code; claude subscription selection will not silently change billing routes"
            )
    if workspace is not None:
        _check_project_settings(Path(workspace) / ".claude" / "settings.json")


def _check_project_settings(path: Path) -> None:
    """Refuse project settings that would bill another way or send the login elsewhere."""
    if not path.is_file():
        return
    try:
        settings = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise RuntimeError(
            f"{path} cannot be read as JSON ({error}); Claude Code would skip it without saying so, so its "
            "billing settings cannot be checked. Fix or remove it; no provider request was launched"
        ) from error
    if not isinstance(settings, dict):
        raise RuntimeError(f"{path} is not a JSON object; fix or remove it; no provider request was launched")
    environment = settings.get("env")
    if not isinstance(environment, dict):
        environment = {}
    found = [key for key in PROJECT_BILLING_SETTINGS if settings.get(key) not in (None, "")]
    found += [
        f"env.{name}"
        for name, value in environment.items()
        if _PROJECT_BILLING_ENV.fullmatch(str(name)) and str(value) not in _FALSE
    ]
    if found:
        raise RuntimeError(
            f"{path} sets {', '.join(found)}, which Claude Code applies to every stage "
            "(--setting-sources project) and would change how it bills or where it sends your login; "
            "claude subscription selection will not silently change billing routes"
        )


def _probe(workspace, env) -> tuple[str, Callable[[list[str]], Any], dict[str, str], str | None]:
    """The resolved ``claude`` and a runner for its preflight commands, in the child environment."""
    cwd = str(workspace) if workspace is not None and Path(workspace).is_dir() else None
    effective = child_environment(env)
    executable = env_prep.resolve_executable("claude", effective, cwd=cwd, allow_default_path=env is None)
    if not executable:
        raise RuntimeError(NOT_ON_PATH)

    def call(command: list[str]) -> Any:
        return env_prep.preflight_run(
            command,
            effective,
            require_executable=env is not None,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )

    return executable, call, effective, cwd


def _version(executable: str, call: Callable[[list[str]], Any]) -> str:
    try:
        result = call([executable, "--version"])
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("claude version check timed out; no agent was launched") from error
    except OSError as error:
        raise RuntimeError(f"claude version check cannot start: {error}; no agent was launched") from error
    if result.returncode:
        raise RuntimeError(f"claude version check failed (exit {result.returncode}); no agent was launched")
    text = ((result.stdout or "") or (result.stderr or "")).strip()
    return parse_version(text) or text


def login_check(workspace=None, *, env: Mapping[str, str] | None = None, run=None) -> dict:
    """Claude Code installed and logged in to a Claude subscription, in the agent's environment.

    ``run`` is doctor's injected command runner (tests): when given, it is the only thing called.
    Returns the version and login method only; never the account's email, organization or plan.
    ``auth status`` is necessary, not sufficient: a stage's own call proves it authenticates.
    """
    if run is None:
        executable, call, _, _ = _probe(workspace, env)
    else:
        executable, call = "claude", lambda command: run(command)
    version = _version(executable, call)
    try:
        result = call([executable, "auth", "status", "--json"])
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"cannot verify Claude Code login: {error}; no provider request was launched") from error
    try:
        status = json.loads(result.stdout or "")
    except ValueError:
        status = None
    if not isinstance(status, dict) or status.get("loggedIn") is not True:
        message = (
            f"Claude Code is not logged in (claude auth status --json); {_LOGIN}, then resume. "
            "No provider request was launched."
        )
        hint = provider_env.withheld_hint(agent_env.withheld(os.environ)) if env is None and run is None else ""
        raise RuntimeError(message + (" " + hint if hint else ""))
    provider = status.get("apiProvider")
    if provider != "firstParty":
        raise RuntimeError(
            f"Claude Code would bill through {provider} (CLAUDE_CODE_USE_BEDROCK, CLAUDE_CODE_USE_VERTEX or a "
            "gateway) instead of your Claude login; claude subscription selection will not silently change "
            "billing routes"
        )
    method, key_source = status.get("authMethod"), status.get("apiKeySource")
    if method not in SUBSCRIPTION_AUTH or (key_source and key_source != "none"):
        source = f", key from {key_source}" if key_source and key_source != "none" else ""
        raise RuntimeError(
            f"Claude Code would bill an API key ({method}{source}); the built-in claude provider runs only on a "
            "Claude subscription login; claude subscription selection will not silently change billing routes"
        )
    return {
        "executable": executable,
        "version": version,
        "loggedIn": True,
        "authMethod": method,
        "apiProvider": provider,
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_settings(workspace=None, *, env: Mapping[str, str] | None = None) -> dict:
    """The transport identity a saved run is checked against before every stage."""
    check_forbidden_environment(env, workspace=workspace)
    executable, call, effective, _ = _probe(workspace, env)
    version = _version(executable, call)
    settings = Path(workspace) / ".claude" / "settings.json" if workspace is not None else None
    try:
        project = _sha256(settings.read_bytes()) if settings is not None and settings.is_file() else None
    except OSError:
        project = None
    policy = json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
    return {
        "engine": NAME,
        "identity_version": 2,
        "adapter": ADAPTER,
        "launch_policy_sha256": _sha256(policy),
        "executable": executable,
        "version": version,
        "project_settings_sha256": project,
        "environment_sha256": {
            name: _sha256(effective[name].encode()) if name in effective else None for name in HASHED_ENV
        },
    }


def transport_drift(current, checkpoint) -> bool:
    return current != checkpoint


def check_models(roles, workspace=None, *, env=None, inventory=None) -> None:
    available = inventory if inventory is not None else set(LISTED_MODELS)
    missing = []
    for entry in roles.values():
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
            raise RuntimeError("claude model names must be non-empty and contain no whitespace")
        if model not in available:
            missing.append(model)
        effort = entry.get("reasoning_effort")
        if effort and effort not in EFFORTS:
            raise RuntimeError(f"claude reasoning effort must be one of {', '.join(EFFORTS)}")
    if missing:
        raise RuntimeError(
            "Models unavailable in claude: "
            + ", ".join(sorted(set(missing)))
            + "; `autocode models --provider claude` lists what it offers"
        )


def available_models(workspace=None, *, env=None) -> set[str]:
    return set(LISTED_MODELS)


def list_models(workspace=None, *, env=None) -> list[str]:
    return sorted(LISTED_MODELS)


def check_subscription_routes(roles, workspace=None, *, env=None) -> dict:
    """Every Claude role runs on the one subscription login: refuse a billing override, then check it."""
    check_forbidden_environment(env, workspace=workspace)
    return login_check(workspace, env=env)


def launch(
    role,
    workspace,
    run_dir,
    session,
    model,
    effort,
    allow_write,
    *,
    planning=False,
    report=None,
    schema=None,
    prompt_file=None,
    sandbox=None,
    env=None,
) -> tuple[list[str], dict, dict]:
    """The stage's argv (the translator wrapping ``claude -p``), its environment and its launch facts."""
    if not model:
        raise ValueError("claude needs a model; no provider request was launched")
    if effort and effort not in EFFORTS:
        raise ValueError(f"claude reasoning effort must be one of {', '.join(EFFORTS)}")
    if schema is None:
        raise ValueError("claude needs a report schema; no provider request was launched")
    sandbox = sandbox or ("workspace-write" if allow_write and not planning else "read-only")
    # Judging stages run with workspace-write (units/common.py) and keep Edit and Write, as the qualified
    # example did; the after-stage snapshot still catches a judge's source edit.
    writes = sandbox != "read-only"
    child = child_environment(env)
    executable = env_prep.resolve_executable("claude", child, cwd=str(workspace))
    if not executable:
        raise RuntimeError(NOT_ON_PATH)
    argv = [
        sys.executable,
        "-I",
        str(STREAM),
        "--ledger",
        str(Path(run_dir) / "claude-sessions"),
        "--schema",
        str(schema),
        *(["--raw", str(Path(report).with_suffix(".claude.jsonl"))] if report else []),
        *(["--resumed", session] if session else []),
        "--",
        executable,
        "-p",
        *BASE_FLAGS,
        "--model",
        model,
        *(["--effort", effort] if effort else []),
        *(["--resume", session] if session else []),
        "--permission-mode",
        "acceptEdits" if writes else "default",
        "--append-system-prompt",
        prompts.get("fragments/provider-claude/system.md").strip(),
        "--allowedTools",
        *(WRITE_ALLOWED if writes else READ_ALLOWED),
        "--disallowedTools",
        *(() if writes else READ_DISALLOWED),
        *DENY_GIT,
    ]
    return argv, child, {"provider": NAME, "sandbox": sandbox, "report": str(report or "")}


def prompt_for_schema(prompt, schema, events) -> str:
    """The stage prompt with Claude's output contract and the report schema before the handoff data."""
    prompt = tool_handoff.with_capture_command(prompt)
    contract = (
        prompts.get("fragments/provider-claude/prompt-for-schema.md")
        + str(events)
        + prompts.get("fragments/provider-claude/prompt-for-schema-02.md")
        # Compact, as OpenCode's: an indented schema doubled to 19 KB and tipped a repair over its cap (#1337).
        + json.dumps(schema, separators=(",", ":"))
        + "\n"
    )
    return prompt.replace("\nCURRENT HANDOFF DATA\n", contract + "\nCURRENT HANDOFF DATA\n", 1)


def raw_events(path) -> list[dict]:
    rows: list[dict] = []
    file = Path(path)
    if not file.exists():
        return rows
    for line in file.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def normalized_events(rows) -> list[dict]:
    return [row for row in rows if isinstance(row, dict)]


def _legacy_report(path) -> dict:
    """A stage recorded by the pre-#717 example tool (report_file): its report is ``<events>.json``."""
    report_path = Path(path).with_suffix(".json")
    try:
        value = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Tool did not write a JSON report at {report_path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"Tool report at {report_path} must be one JSON object")
    return value


def _messages(rows) -> list[tuple[str, str]]:
    return [
        (str(row["item"].get("id")), row["item"]["text"])
        for row in rows
        if row.get("type") == "item.completed"
        and isinstance(row.get("item"), dict)
        and row["item"].get("type") == "agent_message"
        and isinstance(row["item"].get("text"), str)
    ]


def final_report(path, *, recover_wrapped=False, response_path=None) -> dict:
    """The report Claude Code accepted, from the stage's ``structured_output`` item; never a rejected one."""
    rows = raw_events(path)
    if response_path is None and not any(row.get("type") == "thread.started" for row in rows):
        return _legacy_report(path)
    terminal = [row for row in rows if row.get("type") in ("turn.completed", "turn.failed")]
    if not terminal or terminal[-1].get("type") != "turn.completed":
        raise RuntimeError("Claude Code turn has no successful terminal result; preserve it without automatic retry")
    messages = _messages(rows)
    texts = dict(messages)
    text = (
        texts.get(claude_stream.REPORT_ITEM)
        or texts.get(claude_stream.REJECTED_ITEM)
        or texts.get(claude_stream.RESULT_TEXT_ITEM)
        or (messages[-1][1] if messages else "")
    )
    if response_path is not None:
        Path(response_path).write_text(text)
    if claude_stream.REPORT_ITEM in texts:
        try:
            value = json.loads(texts[claude_stream.REPORT_ITEM])
        except ValueError as error:
            raise RuntimeError(FORMAT_REJECTION) from error
        if isinstance(value, dict):
            return value
    raise RuntimeError(FORMAT_REJECTION)


def incomplete_response(path) -> str | None:
    """Text from a stage Claude Code cut at its output token limit: the partial report, else the last
    message. Never a report: callers label it incomplete and keep the events as the evidence source."""
    rows = raw_events(path)
    if not rows or rows[-1].get("type") != "turn.failed":
        return None
    error = rows[-1].get("error")
    if not isinstance(error, dict) or error.get("code") != "output_token_limit":
        return None
    started = max((index for index, row in enumerate(rows) if row.get("type") == "turn.started"), default=-1)
    texts: dict[str, str] = {}
    for row in rows[started + 1 :]:
        item = row.get("item")
        if (
            row.get("type") in ("item.updated", "item.completed")
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            texts[str(item.get("id"))] = item["text"]
    text = texts.get(claude_stream.PARTIAL_ITEM)
    if text is None:
        last = [value for key, value in texts.items() if key != claude_stream.PARTIAL_ITEM]
        text = last[-1] if last else ""
    text = text.strip()
    return text if text and len(text.encode("utf-8")) <= claude_stream.PARTIAL_LIMIT else None
