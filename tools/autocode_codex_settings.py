"""Read the nonsecret Codex configuration and its bounded login status.

``billing_refusal`` is the one answer to "would a run refuse this Codex login": the run's billing
gate (autocode_configure.check_subscription), the Connections check and the engine a new run takes
(autocode_connected_engine) all read it, so Connections never says ready for a login a run refuses.
"""

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

KEY_VARIABLES = ("OPENAI_API_KEY", "CODEX_API_KEY")
ENDPOINT_VARIABLE = "OPENAI_BASE_URL"
CONFIG_KEYS = ("model", "model_reasoning_effort", "model_provider", "openai_base_url")


def config_path(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"


def identity(login_output: str, *, environ=None, config=None) -> dict:
    """The nonsecret settings a run binds, from ``codex login status`` output, the environment and Codex's config.

    ``config`` is Codex's parsed ``config.toml`` (read here when None). Only the auth mode is taken from the
    login output, never an account name or token."""
    environ = os.environ if environ is None else environ
    if config is None:
        path = config_path(environ)
        config = tomllib.loads(path.read_text()) if path.exists() else {}
    settings = {key: config.get(key) for key in CONFIG_KEYS}
    settings["auth_mode"] = "ChatGPT" if "using ChatGPT" in (login_output or "") else "unknown"
    settings["environment_auth_present"] = any(name in environ for name in KEY_VARIABLES)
    settings["environment_base_url_present"] = ENDPOINT_VARIABLE in environ
    return settings


def billing_refusal(settings) -> str | None:
    """Why a run refuses this Codex login, in plain words naming each problem and its fix; None when it accepts it.

    Codex runs only on a ChatGPT subscription and AutoCode never bills another route (#2055). The old
    refusal named all four conditions at once, so a person could not tell which one their machine broke."""
    problems, fixes = [], []
    if settings.get("auth_mode") != "ChatGPT":
        problems.append("Codex is not signed in with a ChatGPT account (it may be using an API key)")
        fixes.append("sign in to Codex with ChatGPT from Connections")
    if settings.get("environment_auth_present"):
        problems.append(
            "an OpenAI API key is set in the environment AutoCode starts from (" + " or ".join(KEY_VARIABLES) + ")"
        )
        fixes.append("remove that variable from the shell profile or launcher that sets it, then restart VS Code")
    if settings.get("model_provider") not in (None, "openai") or settings.get("openai_base_url"):
        problems.append("Codex's config.toml points it at another provider or endpoint")
        fixes.append("remove model_provider and openai_base_url from Codex's config.toml")
    if settings.get("environment_base_url_present"):
        problems.append(f"{ENDPOINT_VARIABLE} sends OpenAI requests to another endpoint")
        fixes.append(f"remove {ENDPOINT_VARIABLE} from the environment AutoCode starts from")
    if not problems:
        return None
    return (
        "Codex runs only on a ChatGPT subscription, and AutoCode never bills another route, but "
        + "; ".join(problems)
        + ". To use Codex, "
        + ", and ".join(fixes)
        + ". To use an API key instead, connect it in OpenCode or Kilo and choose that backend for this chat."
    )


def local_settings():
    """Read only nonsecret settings; never open auth.json or emit auth headers."""
    path = config_path()
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    try:
        auth = captured.run_metadata(["codex", "login", "status"], timeout=CONNECTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Codex login check timed out; no provider request was launched") from error
    except OSError as error:  # a bare errno told nobody which engine or what to do (#2307)
        raise RuntimeError(
            "Codex is not on PATH; no provider request was launched. Run `autocode doctor` to see what this "
            "host has: an installed Claude Code or OpenCode runs with --engine claude or --engine opencode"
        ) from error
    return identity(auth.stdout + auth.stderr, config=config)


def login_refusal(login_output: str, *, environ=None) -> str | None:
    """``billing_refusal`` for a login whose ``codex login status`` output is in hand; an unreadable config
    is left to the run, which reports it."""
    try:
        settings = identity(login_output, environ=environ)
    except (OSError, ValueError):
        settings = identity(login_output, environ=environ, config={})
    return billing_refusal(settings)
