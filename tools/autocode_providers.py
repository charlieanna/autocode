"""Provider discovery and run-boundary selection.

The workflow engine calls one provider facade. OpenCode is built in. Every
other name loads a TOML config; there is no Python plug-in path.
"""
from __future__ import annotations

import re
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


def select(requested: str | None, saved: Mapping[str, object] | None) -> str:
    """Choose a provider once, retaining it across run resume boundaries."""
    saved_name = saved.get("provider") if saved else None
    if saved_name is not None and not isinstance(saved_name, str):
        raise ValueError("saved provider selection is malformed")
    selected = requested or saved_name or "opencode"
    if saved_name is not None and requested is not None and requested != saved_name:
        raise ValueError("cannot change provider while resuming a run; start a new run instead")
    if not isinstance(selected, str) or not _PROVIDER_NAME.fullmatch(selected):
        raise ValueError("provider names use lowercase letters, digits, and underscores")
    return selected
