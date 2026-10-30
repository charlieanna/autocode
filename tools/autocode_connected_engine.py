"""The engine a new run takes from the accounts this machine has connected (#2049).

A person may hold any mix of subscriptions and API keys: a Claude subscription in
Claude Code, a ChatGPT login in Codex, and every other account or API key (Z.ai, MiMo,
OpenAI, Anthropic) in OpenCode or Kilo. Those four are the backends AutoCode offers (owner
decision 2026-10-30); the AutoCode direct API-key connection is retired and is never chosen
here, though a run that names ``--provider autocode`` still runs on it. Without this module a run that names no engine runs OpenCode, and on a
machine without OpenCode it stops at "opencode is not on PATH" while a ready Claude
Code sits beside it; the person then has to learn ``--engine claude`` and type it on
every command.

``choose`` fills that one gap and nothing more: when no explicit choice exists and
OpenCode is not installed, it takes the first connected backend in ORDER and says why.
It never overrides a choice (a flag, ``AUTOCODE_PROVIDER``, ``default_provider`` in the
user's config, the project's ``engine``), never touches a saved run, launches no model
and reads no credential: each check is the backend's own login listing, the one
``autocode connections check`` runs. The order is the proposed answer to the
"default engine when several are logged in" row of #2054; a decision there changes
ORDER, not callers.

Layer: runtime helper over the provider facades. It imports nothing from the
controller, the CLI or the configuration module that calls it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

try:
    from . import autocode_providers as providers
    from . import autocode_route_discovery as route_discovery
except ImportError:
    import autocode_providers as providers
    import autocode_route_discovery as route_discovery

# (connection id, engine, provider, label) in the order a connected one is taken. Codex is
# an engine; the others run on the OpenCode facade engine as providers (#717, #2330).
ORDER = (
    ("claude", "opencode", "claude", "Claude Code"),
    ("codex", "codex", None, "Codex"),
    ("kilocode", "opencode", "kilocode", "Kilo"),
)
EXECUTABLES = {"claude": "claude", "codex": "codex", "kilocode": "kilo"}


def _run(command, cwd=None):
    return subprocess.run(command, capture_output=True, text=True, cwd=cwd, timeout=45)


def explicit_choice(args) -> bool:
    """Whether the command line or the user's config already names the engine or provider.

    A ``--engine claude`` arrives as engine ``opencode`` + provider ``claude`` (role_transport
    .engine_provider), and a project's ``engine`` is applied to ``args`` before configuration,
    so a None engine and a None provider mean nobody chose.
    """
    if getattr(args, "engine", None) or getattr(args, "provider", None):
        return True
    try:
        return providers.default_choice()[1] != "built-in default"
    except (OSError, ValueError):
        # An unreadable user config is reported by the run itself; it is still a choice.
        return True


def apply(args, state, workspace=None, *, say=None) -> dict | None:
    """Route a new run that nobody routed, in place on ``args``, and say so once.

    For a run with saved settings, sessions or history nothing changes: it keeps its engine.
    Otherwise, when ``explicit_choice`` is false and ``choose`` names a backend, ``args.engine``
    and ``args.provider`` take that backend exactly as ``--engine``/``--provider`` would have set
    them, the choice is kept on ``args._connected_engine``, and one line names it.
    """
    started = bool(state.get("settings") or state.get("sessions") or state.get("history"))
    if started or getattr(args, "_connected_engine", None) is not None or explicit_choice(args):
        return getattr(args, "_connected_engine", None)
    chosen = choose(workspace=workspace)
    if chosen is None:
        return None
    args.engine, args.provider, args._connected_engine = chosen["engine"], chosen["provider"], chosen
    # A notice, not a result: stderr, so --json output and captured reports stay what they were.
    (say or _say)(f"Engine for this run: {chosen['label']}. {chosen['reason']}.")
    return chosen


def _say(line):
    print(line, file=sys.stderr, flush=True)


def choose(*, which=shutil.which, runner=_run, workspace=None, resolve=providers.resolve) -> dict | None:
    """The connected backend a new run takes when nothing chose one, or None to keep OpenCode.

    Returns ``{"id", "engine", "provider", "label", "reason", "skipped"}``: ``engine`` and
    ``provider`` as configuration takes them, ``reason`` one sentence for the person,
    ``skipped`` the backends looked at first and why each was passed over. None means the
    default stands: OpenCode is installed (its own checks then apply), or nothing is connected
    (the run's existing OpenCode refusal names what to install). An injected ``which`` and
    ``runner`` (doctor, tests) are the only things consulted; no host command runs then.
    """
    if which("opencode"):
        return None
    skipped: list[dict] = []
    for name, engine, provider, label in ORDER:
        why = _not_connected(name, which, runner, workspace, resolve)
        if why is None:
            return {
                "id": name,
                "engine": engine,
                "provider": provider,
                "label": label,
                "reason": (
                    f"{label} is connected and no engine is configured (OpenCode is not installed); "
                    "pass --engine or --provider, or set default_provider in ~/.config/autocode/config.toml, "
                    "to choose another"
                ),
                "skipped": skipped,
            }
        skipped.append({"id": name, "label": label, "why": why})
    return None


def _not_connected(name, which, runner, workspace, resolve) -> str | None:
    """None when ``name`` is installed and signed in, else one short reason."""
    executable = EXECUTABLES.get(name)
    if executable and not which(executable):
        return f"{executable} is not on PATH"
    try:
        if name == "claude":
            facade = resolve("claude")
            facade.check_forbidden_environment(workspace=workspace)
            facade.login_check(workspace, run=runner)
            return None
        if name == "codex":
            return None if runner(["codex", "login", "status"]).returncode == 0 else "codex is not logged in"
        if name == "kilocode":
            facade = resolve("kilocode")
            if route_discovery.discover(facade, workspace)["accounts"]:
                return None
            return "kilo has no connected account (kilo auth login)"
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        return str(error).splitlines()[0]
    return f"{name} is not a connected backend"
