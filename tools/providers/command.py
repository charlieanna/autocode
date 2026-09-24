"""Config-registered command tools.

A tool joins Autocode by shipping a TOML file. This module turns that file into
the same facade OpenCode exposes. The tool writes its final JSON report to the
path Autocode passes; Autocode does not parse the tool's event stream.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib


REQUIRED_ROLES = ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
PLACEHOLDERS = {"model", "effort", "workspace", "report", "schema", "prompt_file", "run_dir", "role", "sandbox"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}$")


class CommandProvider:
    """One loaded provider config. Attribute names match the OpenCode facade."""

    SUPPORTS_SESSIONS = False

    def __init__(self, config: dict, path: Path):
        self._config = config
        self._path = path
        self.DEFAULT_MODELS = {role: spec["model"] for role, spec in config["roles"].items()}
        self.DEFAULT_REASONING_EFFORTS = {role: spec["effort"] for role, spec in config["roles"].items()}

    def local_settings(self, workspace=None):
        command = self._config["command"]
        executable = shutil.which(command[0])
        if not executable:
            raise RuntimeError(f"{self._config['name']} command {command[0]!r} is not on PATH")
        version = None
        version_command = self._config.get("version_command")
        if version_command:
            try:
                result = subprocess.run(version_command, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"{self._config['name']} version check timed out; no agent was launched") from error
            if result.returncode:
                raise RuntimeError(f"{self._config['name']} version command failed; no agent was launched")
            version = (result.stdout or result.stderr).strip()
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

    def check_models(self, roles, workspace=None):
        available = self._available_models(workspace)
        missing = []
        for entry in roles.values():
            model = entry.get("model")
            if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
                raise RuntimeError(f"{self._config['name']} model names must be non-empty and contain no whitespace")
            if available is not None and model not in available:
                missing.append(model)
        if missing:
            raise RuntimeError(f"Models unavailable in {self._config['name']}: " + ", ".join(sorted(set(missing))))

    def check_subscription_routes(self, roles, workspace=None):
        """The registered tool owns its login. Autocode does not inspect it."""
        return None

    def launch(self, role, workspace, run_dir, session, model, effort, allow_write, *,
               planning=False, report=None, schema=None, prompt_file=None, sandbox=None):
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
        command = [self._fill(part, values) for part in self._config["command"]]
        return command, dict(os.environ), {"provider": self._config["name"], "sandbox": sandbox, "report": str(report or "")}

    def prompt_for_schema(self, prompt, schema, events):
        report = str(Path(events).with_suffix(".json"))
        instructions = (
            "\nTOOL OUTPUT CONTRACT\n"
            "Write your final report as exactly one JSON object to this file: " + report + "\n"
            "Do not wrap it in explanation. The runner reads that file and validates every required field.\n"
            "Cite command evidence only through capture_command receipt files. Do not cite event: IDs. "
            "Use the capture_command in CURRENT HANDOFF DATA with "
            "--output .autocode/evidence/<unique-name>.json -- <command>, then cite that receipt path "
            "in checks and criterion evidence. Never create or edit a receipt manually.\n"
            + json.dumps(schema, indent=2) + "\n")
        return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)

    def final_report(self, path):
        report_path = Path(path).with_suffix(".json")
        try:
            value = json.loads(report_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Tool did not write a JSON report at {report_path}: {error}") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"Tool report at {report_path} must be one JSON object")
        return value

    def raw_events(self, path):
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
        return [row for row in rows if isinstance(row, dict)]

    def _available_models(self, workspace):
        listed = self._config.get("models")
        if listed is not None:
            return set(listed)
        command = self._config.get("models_command")
        if not command:
            return None
        try:
            result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"{self._config['name']} model listing timed out; no agent was launched") from error
        if result.returncode:
            raise RuntimeError(f"Cannot list {self._config['name']} models")
        return set(result.stdout.splitlines())

    @staticmethod
    def _fill(part, values):
        def replace(match):
            key = match.group(1)
            if key not in PLACEHOLDERS:
                raise ValueError(f"unknown command placeholder {{{key}}}")
            return values[key]
        return _PLACEHOLDER.sub(replace, part)


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
    for part in command:
        unknown = set(_PLACEHOLDER.findall(part)) - PLACEHOLDERS
        if unknown:
            raise ValueError("unknown command placeholder " + ", ".join("{" + item + "}" for item in sorted(unknown)))
        if "{" in _PLACEHOLDER.sub("", part):
            raise ValueError("command placeholders must look like {model}; other braces are not allowed")
    prompt = config.get("prompt", "stdin")
    if prompt not in ("stdin", "file"):
        raise ValueError("provider prompt must be stdin or file")
    if prompt == "file" and not any("{prompt_file}" in part for part in command):
        raise ValueError("prompt = \"file\" requires {prompt_file} in the command")
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
