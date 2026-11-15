"""Read the nonsecret Codex configuration and its bounded login status."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

try:
    from . import autocode_captured_process as captured
    from .providers.env_prep import CONNECTION_TIMEOUT_SECONDS
except ImportError:
    import autocode_captured_process as captured
    from providers.env_prep import CONNECTION_TIMEOUT_SECONDS


def local_settings():
    """Read only nonsecret settings; never open auth.json or emit auth headers."""
    config_path = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
    keys = ("model", "model_reasoning_effort", "model_provider", "openai_base_url")
    settings = {k: config.get(k) for k in keys}
    try:
        auth = captured.run_metadata(["codex", "login", "status"], timeout=CONNECTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Codex login check timed out; no provider request was launched") from error
    except OSError as error:  # a bare errno told nobody which engine or what to do (#2307)
        raise RuntimeError(
            "Codex is not on PATH; no provider request was launched. Run `autocode doctor` to see what this "
            "host has: an installed Claude Code or OpenCode runs with --engine claude or --engine opencode"
        ) from error
    message = auth.stdout + auth.stderr
    settings["auth_mode"] = "ChatGPT" if "using ChatGPT" in message else "unknown"
    settings["environment_auth_present"] = any(k in os.environ for k in ("OPENAI_API_KEY", "CODEX_API_KEY"))
    settings["environment_base_url_present"] = "OPENAI_BASE_URL" in os.environ
    return settings
