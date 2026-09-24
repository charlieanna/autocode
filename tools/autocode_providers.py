"""Provider discovery and run-boundary selection.

The workflow engine calls one provider facade. OpenCode is built in. Every
other name loads a TOML config; there is no Python plug-in path.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tomllib
from typing import Mapping

_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}$")


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
    try:
        from .providers import command
    except ImportError:
        from providers import command
    return command.load(name)


def user_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return root / "autocode" / "config.toml"


def default_name() -> str:
    """The provider new runs use when --provider is omitted.

    AUTOCODE_PROVIDER wins, then default_provider in the user's
    ~/.config/autocode/config.toml, then the built-in OpenCode. A project folder
    cannot set it.
    """
    selected = os.environ.get("AUTOCODE_PROVIDER")
    source = "AUTOCODE_PROVIDER"
    if not selected:
        path = user_config_path()
        if not path.is_file():
            return "opencode"
        try:
            config = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{path} is not valid TOML: {error}") from error
        selected = config.get("default_provider")
        source = f"default_provider in {path}"
        if selected is None:
            return "opencode"
    if not isinstance(selected, str) or not _PROVIDER_NAME.fullmatch(selected):
        raise ValueError(f"{source} must be a provider name of lowercase letters, digits, and underscores")
    return selected


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
