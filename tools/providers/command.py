"""Config-registered command tools.

A tool joins Autocode by shipping a TOML file. This module turns that file into
the same facade OpenCode exposes. With ``output = "report_file"`` the tool
writes its final JSON report to the path Autocode passes. With
``output = "opencode_events"`` the tool prints OpenCode-format JSON events, and
Autocode reads sessions, usage, command evidence and the final report from them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

try:
    from . import codex_sandbox, env_prep
    from . import opencode as _opencode_events
except ImportError:  # Script-style execution from tools/.
    from providers import codex_sandbox, env_prep
    from providers import opencode as _opencode_events


try:
    from .. import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_tool_handoff as tool_handoff


REQUIRED_ROLES = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
PLACEHOLDERS = {
    "model",
    "effort",
    "workspace",
    "report",
    "schema",
    "prompt_file",
    "run_dir",
    "role",
    "sandbox",
    "sandbox_args",
}
RESUME_PLACEHOLDERS = (PLACEHOLDERS - {"sandbox_args"}) | {"session"}
OUTPUTS = ("report_file", "opencode_events")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}$")
_LITERAL_OPEN = "\x00AUTOCODE_LITERAL_OPEN\x00"
_LITERAL_CLOSE = "\x00AUTOCODE_LITERAL_CLOSE\x00"


class CommandProvider:
    """One loaded provider config. Attribute names match the OpenCode facade."""

    CONFIGURED = True

    def __init__(self, config: dict, path: Path):
        self._config = config
        self._path = path
        self.NAME = config["name"]
        self.OUTPUT = config.get("output", "report_file")
        self.SUPPORTS_SESSIONS = self.OUTPUT == "opencode_events"
        self.PROMPT_MODE = config.get("prompt", "stdin")
        self.DEFAULT_MODELS = {role: spec["model"] for role, spec in config["roles"].items()}
        self.DEFAULT_REASONING_EFFORTS = {role: spec["effort"] for role, spec in config["roles"].items()}
        if "glm" in self.DEFAULT_MODELS:
            self.DEFAULT_MODELS["requirements"] = self.DEFAULT_MODELS["glm"]
            self.DEFAULT_REASONING_EFFORTS["requirements"] = self.DEFAULT_REASONING_EFFORTS["glm"]
        # The models the config lists, or None when it lists them with models_command or not at all.
        self.LISTED_MODELS = config.get("models")
        # The tool's own stronger Builder and the model its checkers move to ([builder_retry]), or None.
        self.BUILDER_RETRY = config.get("builder_retry")

    def local_settings(self, workspace=None, *, env=None):
        effective = env_prep.snapshot_environment(env)
        command = self._config["command"]
        cwd = workspace if env is not None else None
        executable = env_prep.resolve_executable(command[0], effective, cwd=cwd, allow_default_path=env is None)
        if not executable:
            raise RuntimeError(
                f"{self._config['name']} command {command[0]!r} is not on PATH; no provider request was launched"
            )
        version = None
        version_command = self._config.get("version_command")
        if version_command:
            try:
                result = env_prep.preflight_run(
                    version_command,
                    effective,
                    cwd=cwd,
                    require_executable=env is not None,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"{self._config['name']} version check timed out; no agent was launched") from error
            except OSError as error:
                raise RuntimeError(
                    f"{self._config['name']} version check cannot start; no agent was launched"
                ) from error
            if result.returncode:
                raise RuntimeError(f"{self._config['name']} version command failed; no agent was launched")
            version = (result.stdout or result.stderr).strip()
        if self._config.get("sandbox_adapter") == codex_sandbox.ADAPTER:
            codex_sandbox.check_version(version)
        return {
            "engine": self._config["name"],
            "identity_version": 1,
            "executable": executable,
            "version": version,
            "config_path": str(self._path),
            "config_sha256": hashlib.sha256(self._path.read_bytes()).hexdigest(),
        }

    def transport_drift(self, current, checkpoint):
        return current != checkpoint

    def check_models(self, roles, workspace=None, *, env=None):
        available = self._available_models(workspace, env)
        missing = []
        for entry in roles.values():
            model = entry.get("model")
            if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
                raise RuntimeError(f"{self._config['name']} model names must be non-empty and contain no whitespace")
            if available is not None and model not in available:
                missing.append(model)
        if missing:
            raise RuntimeError(
                f"Models unavailable in {self._config['name']}: "
                + ", ".join(sorted(set(missing)))
                + f"; `autocode models --provider {self._config['name']}` lists what it offers"
            )

    def available_models(self, workspace=None, *, env=None):
        """Models from models/models_command; None when the config lists neither."""
        return self._available_models(workspace, env)

    def list_models(self, workspace=None, *, env=None):
        """Models from models/models_command, or the configured role models when neither is set."""
        available = self._available_models(workspace, env)
        return sorted(available if available is not None else set(self.DEFAULT_MODELS.values()))

    def check_subscription_routes(self, roles, workspace=None, *, env=None):
        """Verify configured subscription routes. Tools without [auth] are not checked."""
        auth = self._config.get("auth")
        if not auth:
            return None
        name = self._config["name"]
        routes = auth.get("routes") or []
        if not routes:
            return None
        # A selected model no declared route covers would otherwise skip verification
        # entirely (#888): refuse instead of launching on an unverified billing route.
        # This reverses the earlier "a model outside every declared route is not this
        # check's business" position at the maintainer's direction (2026-10-10).
        unrouted = sorted(
            {
                str(entry["model"])
                for entry in roles.values()
                if entry.get("model") and not any(str(entry["model"]).startswith(route["models"]) for route in routes)
            }
        )
        if unrouted:
            raise RuntimeError(
                f"{name} has no subscription route for {', '.join(unrouted)}; declare an "
                "[[auth.routes]] entry for that prefix or choose a routed model — "
                "subscription selection will not silently change billing routes"
            )
        selected = [
            route
            for route in routes
            if any(str(entry.get("model", "")).startswith(route["models"]) for entry in roles.values())
        ]
        if not selected:
            return None
        forbidden = [key for key in auth.get("forbid_env", []) if key in env_prep.combined_environment(env)]
        if forbidden:
            raise RuntimeError(
                f"{', '.join(forbidden)} is set; {name} subscription selection will not silently change billing routes"
            )
        try:
            result = env_prep.preflight_run(
                auth["command"],
                env_prep.snapshot_environment(env),
                cwd=workspace,
                require_executable=env is not None,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"cannot verify {name} login; no provider request was launched") from error
        if result.returncode:
            raise RuntimeError(f"cannot verify {name} login; {auth['command'][0]} auth listing failed")
        summary = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout + result.stderr)
        for route in selected:
            found = re.findall(route["pattern"], summary, re.MULTILINE)
            if not found or any(mode != route["expect"] for mode in found):
                raise RuntimeError(
                    f"{name} {route['models']} models require login mode {route['expect']!r}; found {found or 'nothing'}"
                )
        return None

    def launch(
        self,
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
    ):
        if sandbox is None:
            sandbox = "workspace-write" if allow_write and not planning else "read-only"
        values = {
            "model": model or "",
            "effort": effort or "",
            "workspace": str(workspace),
            "report": str(report or ""),
            "schema": str(schema or ""),
            "prompt_file": str(prompt_file or ""),
            "run_dir": str(run_dir),
            "role": role,
            "sandbox": sandbox,
        }
        command = []
        for part in self._config["command"]:
            if part == codex_sandbox.TOKEN:
                command.extend(codex_sandbox.arguments(sandbox, workspace, run_dir, report))
            else:
                command.append(self._fill(part, values))
        if session and self.SUPPORTS_SESSIONS:
            command += [
                self._fill(part, {**values, "session": session}, RESUME_PLACEHOLDERS) for part in self._config["resume"]
            ]
        return (
            command,
            env_prep.child_environment(env),
            {"provider": self._config["name"], "sandbox": sandbox, "report": str(report or "")},
        )

    def prompt_for_schema(self, prompt, schema, events):
        if self.OUTPUT == "opencode_events":
            return _opencode_events.prompt_for_schema(prompt, schema, events)
        prompt = tool_handoff.with_capture_command(prompt)
        report = str(Path(events).with_suffix(".json"))
        persistence = "Write your final report as exactly one JSON object to this file: " + report + "\n"
        if self._config.get("sandbox_adapter") == codex_sandbox.ADAPTER:
            persistence = (
                "Return exactly one JSON object as your final response. Codex persists it at "
                + report
                + "; no shell write of the final report is required.\n"
            )
        else:
            scratch = str(Path(report).with_suffix("")) + "-report-scratch"
            persistence += (
                "Put any temporary report payloads or report-validation scripts only in this run artifact directory: "
                + scratch
                + ". Create that directory if needed. Do not create reporting scratch files in the repository root "
                "or source directories, and do not stage or commit reporting artifacts. Read-only stages must "
                "leave repository source unchanged.\n"
            )
        instructions = (
            "\nTOOL OUTPUT CONTRACT\n"
            + persistence
            + "Do not wrap it in explanation. The runner reads that file and validates every required field.\n"
            "Cite command evidence only through capture_command receipt files. Do not cite event: IDs. "
            "Use the capture_command in CURRENT HANDOFF DATA with "
            "--output .autocode/evidence/<unique-name>.json -- <command>, then cite that receipt path "
            "in checks and criterion evidence. Never create or edit a receipt manually. A check's command is "
            "the command you gave capture after --, copied exactly: never a placeholder such as <tmpdir>, a "
            "summary, or the capture invocation itself. The runner compares it with the receipt.\n"
            + json.dumps(schema, indent=2)
            + "\n"
        )
        return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)

    def final_report(self, path, *, recover_wrapped=False, response_path=None):
        if self.OUTPUT == "opencode_events":
            return _opencode_events.final_report(path, recover_wrapped=recover_wrapped, response_path=response_path)
        report_path = Path(path).with_suffix(".json")
        try:
            text = report_path.read_text()
            if response_path is not None:
                Path(response_path).write_text(text)
            value = json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Tool did not write a JSON report at {report_path}: {error}") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"Tool report at {report_path} must be one JSON object")
        return value

    def raw_events(self, path):
        if self.OUTPUT == "opencode_events":
            return _opencode_events.raw_events(path)
        rows = []
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

    def normalized_events(self, rows):
        if self.OUTPUT == "opencode_events":
            return _opencode_events.normalized_events(rows)
        return [row for row in rows if isinstance(row, dict)]

    def _available_models(self, workspace, env=None):
        listed = self._config.get("models")
        if listed is not None:
            return set(listed)
        command = self._config.get("models_command")
        if not command:
            return None
        try:
            result = env_prep.preflight_run(
                command,
                env_prep.snapshot_environment(env),
                cwd=workspace,
                require_executable=env is not None,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"{self._config['name']} model listing timed out; no agent was launched") from error
        except OSError as error:
            # The models command could not even be spawned (issue #226's failure
            # class): report the honest prelaunch state, never the raw OSError.
            raise RuntimeError(f"Cannot list {self._config['name']} models; no agent was launched") from error
        if result.returncode:
            raise RuntimeError(f"Cannot list {self._config['name']} models; no agent was launched")
        return {
            line.split()[0]
            for line in (line.strip() for line in result.stdout.splitlines())
            if line and not line.startswith("#")
        }

    @staticmethod
    def _fill(part, values, allowed=PLACEHOLDERS):
        part = _mask_literal_braces(part)

        def replace(match):
            key = match.group(1)
            if key not in allowed:
                raise ValueError(f"unknown command placeholder {{{key}}}")
            return values[key]

        return _restore_literal_braces(_PLACEHOLDER.sub(replace, part))


def user_config_path(name: str) -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "autocode" / "providers" / f"{name}.toml"


def bundled_config_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "configs" / f"{name}.toml"


def locate(name: str) -> Path:
    """User config wins. Bundled examples are the fallback. Project trees are never searched."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("provider names use lowercase letters, digits, and underscores")
    user = user_config_path(name)
    if user.is_file():
        return user
    bundled = bundled_config_path(name)
    if bundled.is_file():
        return bundled
    raise RuntimeError(f"no provider config for {name!r}; create ~/.config/autocode/providers/{name}.toml")


def load(name: str) -> CommandProvider:
    path = locate(name)
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"provider config {path} is not valid TOML: {error}") from error
    _validate(name, config)
    return CommandProvider(config, path)


def _validate(name: str, config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError("provider config must be a TOML table")
    if config.get("name") != name:
        raise ValueError(f"provider config name must be {name!r}")
    command = config.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        raise ValueError("provider command must be a non-empty array of strings")
    _validate_template(command, PLACEHOLDERS)
    output = config.get("output", "report_file")
    if output not in OUTPUTS:
        raise ValueError("provider output must be report_file or opencode_events")
    resume = config.get("resume")
    if resume is not None:
        if output != "opencode_events":
            raise ValueError('resume requires output = "opencode_events"')
        if not isinstance(resume, list) or not resume or not all(isinstance(part, str) for part in resume):
            raise ValueError("resume must be a non-empty array of strings")
        _validate_template(resume, RESUME_PLACEHOLDERS)
        if not any("{session}" in _mask_literal_braces(part) for part in resume):
            raise ValueError("resume requires {session}")
    elif output == "opencode_events":
        raise ValueError('output = "opencode_events" requires a resume template such as ["--session", "{session}"]')
    prompt = config.get("prompt", "stdin")
    if prompt not in ("stdin", "file"):
        raise ValueError("provider prompt must be stdin or file")
    if prompt == "file" and not any("{prompt_file}" in _mask_literal_braces(part) for part in command):
        raise ValueError('prompt = "file" requires {prompt_file} in the command')
    roles = config.get("roles")
    if not isinstance(roles, dict):
        raise ValueError("provider config requires a [roles] table")
    missing = [role for role in REQUIRED_ROLES if role not in roles]
    if missing:
        raise ValueError("provider config is missing roles: " + ", ".join(missing))
    for role, spec in roles.items():
        if role not in REQUIRED_ROLES:
            raise ValueError(f"unknown provider role {role!r}")
        if not isinstance(spec, dict):
            raise ValueError(f"role {role} must be a table with model and effort")
        model = spec.get("model")
        effort = spec.get("effort")
        if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
            raise ValueError(f"role {role} needs a model name")
        if not isinstance(effort, str):
            raise ValueError(f"role {role} needs an effort string")
    for key in ("models", "models_command", "version_command"):
        if key not in config:
            continue
        value = config[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"{key} must be an array of non-empty strings")
    if "models" in config and not config["models"]:
        raise ValueError("models must list at least one model")
    codex_sandbox.validate(config)
    if "auth" in config:
        _validate_auth(config["auth"])
    if "builder_retry" in config:
        _validate_builder_retry(config)


BUILDER_RETRY_KEYS = ("strong_model", "checker_model", "strong_effort")


def _validate_builder_retry(config) -> None:
    """The Builder's stronger attempt must be another model the tool serves, and checking it must not be."""
    table = config["builder_retry"]
    if not isinstance(table, dict):
        raise ValueError("[builder_retry] must be a table")
    unknown = sorted(set(table) - set(BUILDER_RETRY_KEYS))
    if unknown:
        raise ValueError(
            "[builder_retry] accepts only " + ", ".join(BUILDER_RETRY_KEYS) + "; not " + ", ".join(unknown)
        )
    for key in ("strong_model", "checker_model"):
        value = table.get(key)
        if not isinstance(value, str) or not value.strip() or any(char.isspace() for char in value):
            raise ValueError(f"[builder_retry] {key} needs a model name")
        if "models" in config and value not in config["models"]:
            raise ValueError(f"[builder_retry] {key} {value} is not in models")
    if "strong_effort" in table and (not isinstance(table["strong_effort"], str) or not table["strong_effort"].strip()):
        raise ValueError("[builder_retry] strong_effort must be a non-empty string")
    if table["strong_model"] == config["roles"]["terra"]["model"]:
        raise ValueError("[builder_retry] strong_model is the Builder's own model; name a stronger one")
    if table["checker_model"] == table["strong_model"]:
        raise ValueError(
            "[builder_retry] checker_model is strong_model, so the stronger attempt would be checked by its own model"
        )


def _validate_auth(auth) -> None:
    if not isinstance(auth, dict):
        raise ValueError("[auth] must be a table")
    command = auth.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part.strip() for part in command)
    ):
        raise ValueError("[auth] command must be a non-empty array of strings")
    forbid = auth.get("forbid_env", [])
    if not isinstance(forbid, list) or not all(isinstance(item, str) and item.strip() for item in forbid):
        raise ValueError("[auth] forbid_env must be an array of environment variable names")
    routes = auth.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("[auth] routes must list at least one route")
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("[auth] routes entries must be tables")
        prefix = route.get("models")
        pattern = route.get("pattern")
        expect = route.get("expect")
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("[auth] route models must be a non-empty prefix")
        if not isinstance(pattern, str):
            raise ValueError("[auth] route pattern must be a regular expression")
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"[auth] route pattern is not a valid regular expression: {error}") from error
        if compiled.groups != 1:
            raise ValueError("[auth] route pattern must have exactly one capture group")
        if not isinstance(expect, str) or not expect.strip():
            raise ValueError("[auth] route expect must be a non-empty string")


def _validate_template(parts, allowed) -> None:
    for part in parts:
        masked = _mask_literal_braces(part)
        if "sandbox_args" in _PLACEHOLDER.findall(masked) and part != codex_sandbox.TOKEN:
            raise ValueError("{sandbox_args} must be a standalone command argument")
        unknown = set(_PLACEHOLDER.findall(masked)) - allowed
        if unknown:
            raise ValueError("unknown command placeholder " + ", ".join("{" + item + "}" for item in sorted(unknown)))
        if "{" in _PLACEHOLDER.sub("", masked) or "}" in _PLACEHOLDER.sub("", masked):
            raise ValueError("command placeholders must look like {model}; other braces are not allowed")


def _mask_literal_braces(part: str) -> str:
    return part.replace("{{", _LITERAL_OPEN).replace("}}", _LITERAL_CLOSE)


def _restore_literal_braces(part: str) -> str:
    return part.replace(_LITERAL_OPEN, "{").replace(_LITERAL_CLOSE, "}")
