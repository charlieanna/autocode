"""Gemini CLI as a built-in provider: every stage is one headless ``gemini`` call.

``--provider gemini`` selects this facade on the OpenCode engine, as a configured tool: bare
``gemini-*`` model ids from the signed-in account's own listing. It also runs single roles of another
run (``--role-transport tester=gemini``), which is how a Claude Code run gets its work checked by a
second vendor. Each stage runs

    python -I providers/gemini_stream.py --raw RUN/...gemini.jsonl --report REPORT -- gemini --model M --output-format stream-json ...

with the stage prompt on standard input. The translator (``gemini_stream``) writes Codex-shaped
events, so the stage's thread, usage, command activity and terminal row are read like a Codex stage's
(``OUTPUT = "codex_events"``). The report is the model's last message when it is one JSON object.

- Sessions: every stage, a same-stage correction included, starts a fresh Gemini session
  (``SUPPORTS_SESSIONS = False``), so the runner reads a stage as finished by its report file, which
  the translator writes once the turn completes; a stage without a JSON report goes to the runner's
  report repair.
- Tools: ``--approval-mode yolo`` so headless calls run, inside two AutoCode policy files written to
  the run directory: every stage is denied git history commands and every command that changes GitHub
  or pushes (``autocode_publish_guard``); a read-only stage also loses ``write_file`` and ``replace``.
  No extensions (``-e none``) and no MCP servers. The runner's after-stage snapshot still catches a
  judge's source edit through the shell, as for every tool.
- Login: Google sign-in only (Gemini CLI's ``oauth-personal``): an API key or Vertex would bill per
  token, and AutoCode withholds key variables from agents anyway. ``login_check`` reads which sign-in
  Gemini CLI is set to and asks Gemini CLI for the account's model listing, which refreshes the login
  without a model call; Gemini CLI's own debug log says when that refresh failed, as it does for a
  Workspace account without ``GOOGLE_CLOUD_PROJECT``.
- Reasoning level: Gemini CLI takes none on its command line, so a stage's level is not sent and the
  model uses its own thinking setting (``EFFORTS`` is empty; a saved level is reported, never passed).
- Identity: the launch policy below (hashed), the resolved executable and ``gemini --version``. A
  change pauses a saved run (``PAUSED_TRANSPORT_CHANGED``). Reviewer rule: a change to what the
  translator's rows mean bumps ``ADAPTER``.

Imports only lower-level helpers, never ``autocode_providers`` or anything above it.
"""

from __future__ import annotations

try:
    from .. import autocode_agent_env as agent_env
    from .. import autocode_changed_code_review as changed_code_review
    from .. import autocode_prompts as prompts
    from .. import autocode_publish_guard as publish_guard
    from .. import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_changed_code_review as changed_code_review
    import autocode_prompts as prompts
    import autocode_publish_guard as publish_guard
    import autocode_tool_handoff as tool_handoff

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

try:
    from . import env_prep, gemini_stream
except ImportError:  # Script-style execution from tools/.
    from providers import env_prep, gemini_stream


CONFIGURED = True
NAME = "gemini"
LABEL = "Gemini CLI"
OUTPUT = "codex_events"
SUPPORTS_SESSIONS = False
RESUME_ACROSS_STAGES = False
PROMPT_MODE = "stdin"
SUPPORTS_REPORT_RECOVERY = False
ADAPTER = "gemini-stream-1"
# Gemini CLI's default catalogue (0.71): what every signed-in account is offered. A subscription may list more
# (preview models); ``available_models`` reads the account's own listing.
PRO, FLASH, LITE = "gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.1-flash-lite"
LISTED_MODELS = [PRO, FLASH, LITE]
# Verifiers differ from producers: Flash drafts and builds, Pro reviews, tests and completes.
DEFAULT_MODELS = {
    "requirements": FLASH,
    "planner": FLASH,
    "plan_reviewer": PRO,
    "builder": FLASH,
    "tester": PRO,
    "completion": PRO,
    "task_planner": PRO,
}
DEFAULT_REASONING_EFFORTS = {role: "" for role in DEFAULT_MODELS}
# The roles that check work, and the ids that name a Pro (a preview of one included; never a variant such as -tts).
CHECKING_ROLES = ("plan_reviewer", "tester", "completion")
_PRO_ID = re.compile(r"gemini-\d+(?:\.\d+)*-pro(?:-preview[\w.-]*)?")
# No stronger Builder attempt: the default catalogue has no model above Pro to give it and still leave Pro to check it.
BUILDER_RETRY = None
EFFORTS: tuple[str, ...] = ()
# The sign-in that bills a Google account's own plan (a free tier, Google AI Pro or Ultra, or a Code Assist
# license); Gemini CLI calls it oauth-personal. The others bill per token.
SUBSCRIPTION_AUTH = "oauth-personal"
AUTH_TYPES = {
    "oauth-personal": "Sign in with Google",
    "gemini-api-key": "a Gemini API key",
    "vertex-ai": "Vertex AI",
    "compute-default-credentials": "Google Cloud default credentials",
    "cloud-shell": "Cloud Shell",
}
# Variables that choose a sign-in other than Google's, in Gemini CLI 0.71's own precedence.
AUTH_ENV = (
    ("GOOGLE_GENAI_USE_GCA", "oauth-personal"),
    ("GOOGLE_GENAI_USE_VERTEXAI", "vertex-ai"),
    ("GEMINI_API_KEY", "gemini-api-key"),
)
DENY_GIT = (
    "git commit",
    "git push",
    "git checkout",
    "git reset",
    "git stash",
    "git branch",
    "git rebase",
    "git merge",
)
READ_ONLY_DENIED = ("write_file", "replace")
BASE_FLAGS = (
    "--output-format",
    "stream-json",
    "--approval-mode",
    "yolo",
    "--skip-trust",
    "-e",
    "none",
    "--allowed-mcp-server-names",
    "autocode-none",
)
# Gemini CLI otherwise starts itself a second time with a larger heap; one process keeps the stage's custody simple.
SET_ENV = {"GEMINI_CLI_NO_RELAUNCH": "true"}
REMOVED_ENV = ("GEMINI_DEBUG_LOG_FILE", "GEMINI_SYSTEM_MD", "GEMINI_CLI_SYSTEM_SETTINGS_PATH")
POLICY = {
    "base_flags": BASE_FLAGS,
    "deny_git": DENY_GIT,
    "deny_publish": publish_guard.COMMANDS,
    "read_only_denied": READ_ONLY_DENIED,
    "set_env": SET_ENV,
    "removed_env": REMOVED_ENV,
}
STREAM = Path(__file__).with_name("gemini_stream.py")
# The exact literal the shared recovery modules match (autocode_report_retry, autocode_format_correction).
FORMAT_REJECTION = "OpenCode final message is not a JSON report; inspect the saved raw events"
_VERSION = re.compile(r"\s*(\d+\.\d+\.\d+)\b")
_LOGIN = "run `gemini` in a terminal and choose Sign in with Google"
_REFRESH_FAILED = "Failed to refresh auth"
_EXPIRED = "Cached credentials are not valid"
NOT_ON_PATH = (
    "gemini is not on PATH; no provider request was launched. Install Gemini CLI with "
    "`npm install -g @google/gemini-cli`, then run `gemini` once to sign in"
)


def parse_version(text: str) -> str | None:
    match = _VERSION.match(text) if isinstance(text, str) else None
    return match.group(1) if match else None


def child_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment Gemini CLI runs in: the agent environment, minus variables that change its prompt or logs."""
    base = agent_env.scrubbed(env_prep.snapshot_environment()) if env is None else dict(env)
    child = {name: value for name, value in base.items() if name not in REMOVED_ENV}
    child.update(SET_ENV)
    return child


def gemini_home(env: Mapping[str, str]) -> Path:
    """Where Gemini CLI keeps its settings and login: ``$GEMINI_CLI_HOME/.gemini``, else ``~/.gemini``."""
    root = env.get("GEMINI_CLI_HOME") or env.get("HOME") or str(Path.home())
    return Path(root) / ".gemini"


def auth_type(env: Mapping[str, str]) -> str | None:
    """The sign-in Gemini CLI will use: its settings' ``security.auth.selectedType``, else the variable that picks one."""
    settings = gemini_home(env) / "settings.json"
    try:
        value = json.loads(settings.read_text())
    except (OSError, ValueError):
        value = None
    selected = (
        (((value or {}).get("security") or {}).get("auth") or {}).get("selectedType")
        if isinstance(value, dict)
        else None
    )
    if isinstance(selected, str) and selected:
        return selected
    for name, kind in AUTH_ENV:
        if env.get(name, "").strip() not in ("", "0", "false"):
            return kind
    return None


def check_forbidden_environment(env: Mapping[str, str] | None = None, *, workspace=None) -> None:
    """Gemini CLI reads its sign-in from its own files; nothing here changes which one a stage uses."""
    return None


def _probe(workspace, env) -> tuple[str, Callable[..., Any], dict[str, str], str | None]:
    cwd = str(workspace) if workspace is not None and Path(workspace).is_dir() else None
    effective = child_environment(env)
    executable = env_prep.resolve_executable("gemini", effective, cwd=cwd, allow_default_path=env is None)
    if not executable:
        raise RuntimeError(NOT_ON_PATH)

    def call(command: list[str], extra: Mapping[str, str] | None = None, timeout: int = 30) -> Any:
        return env_prep.preflight_run(
            command,
            {**effective, **(extra or {})},
            require_executable=env is not None,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    return executable, call, effective, cwd


def _version(executable: str, call: Callable[..., Any]) -> str:
    try:
        result = call([executable, "--version"], timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("gemini version check timed out; no agent was launched") from error
    except OSError as error:
        raise RuntimeError(f"gemini version check cannot start: {error}; no agent was launched") from error
    if result.returncode:
        raise RuntimeError(f"gemini version check failed (exit {result.returncode}); no agent was launched")
    text = ((result.stdout or "") or (result.stderr or "")).strip()
    return parse_version(text) or text


def _listing(executable: str, call: Callable[..., Any]) -> tuple[list[str], str]:
    """The account's model ids and what Gemini CLI logged while refreshing its login for them."""
    with tempfile.TemporaryDirectory(prefix="autocode-gemini-") as folder:
        log = Path(folder) / "debug.log"
        try:
            result = call(
                [executable, "models", "list", "--output-format", "json"], {"GEMINI_DEBUG_LOG_FILE": str(log)}
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("gemini model listing timed out; no agent was launched") from error
        except OSError as error:
            raise RuntimeError(f"gemini model listing cannot start: {error}; no agent was launched") from error
        try:
            logged = log.read_text(errors="replace")
        except OSError:
            logged = ""
    if result.returncode:
        said = gemini_stream.stderr_reason(result.stderr or "")
        raise RuntimeError(f"cannot list Gemini models (exit {result.returncode}): {said or 'no reason given'}")
    try:
        rows = json.loads(result.stdout or "")
    except ValueError as error:
        raise RuntimeError("Gemini CLI's model listing is not JSON; no agent was launched") from error
    models = [
        row["modelId"]
        for row in rows
        if isinstance(rows, list) and isinstance(row, dict) and isinstance(row.get("modelId"), str)
    ]
    return models, logged


def refresh_failure(logged: str) -> str | None:
    """Why Gemini CLI could not refresh its login, from its own debug log, or None.

    Gemini CLI 0.71 lists its default catalogue whatever the login's state, and says what went wrong only
    in that log: an expired or revoked sign-in (``Cached credentials are not valid``), or a refresh that
    failed, such as a Workspace account's missing ``GOOGLE_CLOUD_PROJECT``."""
    if gemini_stream._PROJECT.search(logged) and "requires setting" in logged:
        return gemini_stream.WORKSPACE_HINT
    for line in logged.splitlines():
        if _EXPIRED in line:
            return "your Google sign-in has expired or was revoked; run `gemini` in a terminal and sign in again"
        if _REFRESH_FAILED in line:
            reason = line.split(_REFRESH_FAILED, 1)[1]
            reason = reason.split("falling back to default catalog:", 1)[-1].strip(" :")
            if gemini_stream._PROJECT.search(reason):
                return gemini_stream.WORKSPACE_HINT
            return gemini_stream.stderr_reason(reason) or "Gemini CLI could not refresh its sign-in"
    return None


def login_check(workspace=None, *, env: Mapping[str, str] | None = None, run=None) -> dict:
    """Gemini CLI installed and signed in with Google, in the agent's environment; no model call.

    ``run`` is doctor's injected command runner (tests): when given, it is the only thing called, and
    the sign-in is read from the process environment's Gemini home. Returns the version, the sign-in
    kind and the account's models; never the account's email or project.
    """
    if run is None:
        executable, call, effective, _ = _probe(workspace, env)
    else:
        executable, effective = "gemini", child_environment(env)

        def call(command, extra=None, timeout=30):
            return run(command)

    version = _version(executable, call)
    kind = auth_type(effective)
    if kind is None:
        raise RuntimeError(f"Gemini CLI is not signed in; {_LOGIN}, then resume. No provider request was launched.")
    if kind != SUBSCRIPTION_AUTH:
        raise RuntimeError(
            f"Gemini CLI is set to {AUTH_TYPES.get(kind, kind)}, which bills per token; AutoCode runs Gemini only on "
            f"a Google sign-in. Run `gemini`, type /auth and choose Sign in with Google. No provider request was launched."
        )
    if not (gemini_home(effective) / "oauth_creds.json").is_file():
        raise RuntimeError(
            f"Gemini CLI has no saved Google sign-in; {_LOGIN}, then resume. No provider request was launched."
        )
    models, logged = _listing(executable, call)
    failure = refresh_failure(logged)
    if failure:
        raise RuntimeError(f"Gemini CLI cannot sign in: {failure}. No provider request was launched.")
    return {"executable": executable, "version": version, "loggedIn": True, "authType": kind, "models": models}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def local_settings(workspace=None, *, env: Mapping[str, str] | None = None) -> dict:
    """The transport identity a saved run is checked against before every stage."""
    executable, call, _, _ = _probe(workspace, env)
    version = _version(executable, call)
    policy = json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
    return {
        "engine": NAME,
        "identity_version": 2,
        "adapter": ADAPTER,
        "launch_policy_sha256": _sha256(policy),
        "executable": executable,
        "version": version,
    }


def transport_drift(current, checkpoint) -> bool:
    return current != checkpoint


def _listed(workspace, env) -> list[str]:
    """The account's own models, without Gemini CLI's ``auto`` routers (they pick a model per request)."""
    executable, call, _, _ = _probe(workspace, env)
    models, _logged = _listing(executable, call)
    return [model for model in models if not model.startswith("auto")]


def available_models(workspace=None, *, env=None) -> set[str]:
    try:
        listed = _listed(workspace, env)
    except RuntimeError:
        listed = []
    return set(listed or LISTED_MODELS)


def generation(model) -> tuple[int, ...] | None:
    """The generation a Gemini model's id names (``gemini-2.5-pro`` is (2, 5)), or None when it names none."""
    found = re.match(r"gemini-(\d+(?:\.\d+)*)-", model if isinstance(model, str) else "")
    return tuple(int(part) for part in found.group(1).split(".")) if found else None


def checker_models(listed=None) -> dict[str, str]:
    """The model each checking role runs on an account that lists ``listed``: the newest Pro it offers.

    The catalogue's own Pro is a fixed name, and an account may list a newer one (a subscription's preview
    models). A released Pro is taken before a preview of the same generation, and never one older than the
    catalogue's. Without a listing this is the catalogue's Pro, as ``DEFAULT_MODELS`` gives it.
    """
    pros = [model for model in listed or () if isinstance(model, str) and _PRO_ID.fullmatch(model)]
    newest = max([PRO, *pros], key=lambda model: (generation(model) or (), "-preview" not in model))
    return dict.fromkeys(CHECKING_ROLES, newest)


def older_than_builder(model) -> bool:
    """Whether ``model`` is an older generation than the model this tool builds with by default.

    Such a checker is behind the catalogue's own drafting model (2.5 Pro beside 3.5 Flash), so it must not
    replace another tool's reviewer (autocode_second_vendor)."""
    mine, builder = generation(model), generation(DEFAULT_MODELS["builder"])
    return mine is not None and builder is not None and mine < builder


def list_models(workspace=None, *, env=None) -> list[str]:
    return sorted(available_models(workspace, env=env))


def check_models(roles, workspace=None, *, env=None, inventory=None) -> None:
    available = inventory if inventory is not None else available_models(workspace, env=env)
    missing = []
    for entry in roles.values():
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
            raise RuntimeError("gemini model names must be non-empty and contain no whitespace")
        if model not in available:
            missing.append(model)
    if missing:
        raise RuntimeError(
            "Models unavailable in gemini: "
            + ", ".join(sorted(set(missing)))
            + "; `autocode models --provider gemini` lists what your Google account offers"
        )


def check_subscription_routes(roles, workspace=None, *, env=None) -> dict:
    """Every Gemini role runs on the one Google sign-in: check it."""
    return login_check(workspace, env=env)


def policy_text(read_only: bool) -> str:
    """The Gemini CLI policy a stage runs under: denials only, so yolo allows the rest."""
    commands = [*DENY_GIT, *publish_guard.COMMANDS]
    lines = [
        "# Written by AutoCode for one stage; changes are overwritten.",
        "[[rule]]",
        'toolName = "run_shell_command"',
        "commandPrefix = " + json.dumps(commands),
        'decision = "deny"',
        "priority = 900",
        'denyMessage = "AutoCode stages never change git history, push or change GitHub."',
    ]
    if read_only:
        lines += [
            "",
            "[[rule]]",
            "toolName = " + json.dumps(list(READ_ONLY_DENIED)),
            'decision = "deny"',
            "priority = 900",
            'denyMessage = "This AutoCode stage is read-only."',
        ]
    return "\n".join(lines) + "\n"


def _policy_file(run_dir, read_only: bool) -> Path:
    text = policy_text(read_only)
    folder = Path(run_dir) / "gemini-policy"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{'read-only' if read_only else 'write'}-{_sha256(text.encode())[:12]}.toml"
    if not path.is_file() or path.read_text() != text:
        handle, temporary = tempfile.mkstemp(dir=folder, prefix=".tmp-", suffix=".toml")
        try:
            with os.fdopen(handle, "w") as stream:
                stream.write(text)
            os.replace(temporary, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
    return path


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
    """The stage's argv (the translator wrapping ``gemini``), its environment and its launch facts."""
    if not model:
        raise ValueError("gemini needs a model; no provider request was launched")
    sandbox = sandbox or ("workspace-write" if allow_write and not planning else "read-only")
    child = child_environment(env)
    executable = env_prep.resolve_executable("gemini", child, cwd=str(workspace))
    if not executable:
        raise RuntimeError(NOT_ON_PATH)
    policy = _policy_file(run_dir, sandbox == "read-only")
    argv = [
        sys.executable,
        "-I",
        str(STREAM),
        *(["--raw", str(Path(report).with_suffix(".gemini.jsonl"))] if report else []),
        *(["--report", str(report)] if report else []),
        "--",
        executable,
        "--model",
        model,
        *BASE_FLAGS,
        "--policy",
        str(policy),
    ]
    return argv, child, {"provider": NAME, "sandbox": sandbox, "report": str(report or "")}


def prompt_for_schema(prompt, schema, events) -> str:
    """The stage prompt with Gemini's output contract and the report schema before the handoff data."""
    prompt = tool_handoff.with_capture_command(prompt)
    prompt = tool_handoff.with_stage_events_log(prompt, events)
    schema = changed_code_review.prompt_order(schema)
    contract = (
        prompts.get("fragments/provider-gemini/prompt-for-schema.md") + json.dumps(schema, separators=(",", ":")) + "\n"
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


def final_report(path, *, recover_wrapped=False, response_path=None) -> dict:
    """The JSON object Gemini ended its stage with, from the ``structured_output`` item."""
    rows = raw_events(path)
    terminal = [row for row in rows if row.get("type") in ("turn.completed", "turn.failed")]
    if not terminal or terminal[-1].get("type") != "turn.completed":
        raise RuntimeError("Gemini CLI turn has no successful terminal result; preserve it without automatic retry")
    texts = {
        str(row["item"].get("id")): row["item"]["text"]
        for row in rows
        if row.get("type") == "item.completed"
        and isinstance(row.get("item"), dict)
        and row["item"].get("type") == "agent_message"
        and isinstance(row["item"].get("text"), str)
    }
    text = texts.get(gemini_stream.REPORT_ITEM) or texts.get(gemini_stream.RESULT_TEXT_ITEM) or ""
    if response_path is not None:
        Path(response_path).write_text(text)
    if gemini_stream.REPORT_ITEM in texts:
        try:
            value = json.loads(texts[gemini_stream.REPORT_ITEM])
        except ValueError as error:
            raise RuntimeError(FORMAT_REJECTION) from error
        if isinstance(value, dict):
            return value
    raise RuntimeError(FORMAT_REJECTION)


def incomplete_response(path) -> str | None:
    """Gemini CLI reports no output-limit stop of its own; there is never a partial report to keep."""
    return None
