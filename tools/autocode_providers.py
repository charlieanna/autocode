"""Provider discovery and run-boundary selection.

The workflow engine calls one provider facade. OpenCode and Claude Code are
built in. Every other name loads a TOML config; there is no Python plug-in path.
A user config named after a built-in is never read.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}$")
BUILT_IN = ("opencode", "claude")


def resolve(name: str):
    """Load a provider facade without ever falling back to another provider."""
    if not isinstance(name, str) or not _PROVIDER_NAME.fullmatch(name):
        raise ValueError("provider names use lowercase letters, digits, and underscores")
    if name == "opencode":
        try:
            from .providers import opencode
        except ImportError:  # Script-style execution from tools/.
            from providers import opencode
        return opencode
    if name == "claude":
        try:
            from .providers import claude
        except ImportError:  # Script-style execution from tools/.
            from providers import claude
        return claude
    if name == "kilocode":
        try:
            from .providers import kilocode
        except ImportError:
            from providers import kilocode
        return kilocode.load(name)
    try:
        from .providers import command
    except ImportError:
        from providers import command
    return command.load(name)


def ignored_user_config(name: str) -> Path | None:
    """A user provider config named after a built-in provider: never read, so doctor names it."""
    if name not in BUILT_IN:
        return None
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    path = root / "autocode" / "providers" / f"{name}.toml"
    return path if path.is_file() else None


def user_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "autocode" / "config.toml"


def default_name() -> str:
    """The provider new runs use when --provider is omitted (default_choice)."""
    return default_choice()[0]


def default_choice() -> tuple[str, str]:
    """The provider new runs use when --provider is omitted, and what chose it.

    AUTOCODE_PROVIDER wins, then default_provider in the user's
    ~/.config/autocode/config.toml, then the built-in OpenCode. A project folder
    cannot set it (autocode_project_config).
    """
    selected = os.environ.get("AUTOCODE_PROVIDER")
    source = "AUTOCODE_PROVIDER"
    if not selected:
        path = user_config_path()
        if not path.is_file():
            return "opencode", "built-in default"
        try:
            config = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{path} is not valid TOML: {error}") from error
        selected = config.get("default_provider")
        source = f"default_provider in {path}"
        if selected is None:
            return "opencode", "built-in default"
    if not isinstance(selected, str) or not _PROVIDER_NAME.fullmatch(selected):
        raise ValueError(f"{source} must be a provider name of lowercase letters, digits, and underscores")
    return selected, source


def select(requested: str | None, saved: Mapping[str, object] | None, *, default: str | None = None) -> str:
    """Choose a provider once, retaining it across run resume boundaries."""
    saved_name = saved.get("provider") if saved else None
    if saved_name is not None and not isinstance(saved_name, str):
        raise ValueError("saved provider selection is malformed")
    if saved_name is not None and requested is not None and requested != saved_name:
        raise ValueError("cannot change provider while resuming a run; start a new run instead")
    selected = requested or saved_name or default or default_name()
    if not isinstance(selected, str) or not _PROVIDER_NAME.fullmatch(selected):
        raise ValueError("provider names use lowercase letters, digits, and underscores")
    return selected
