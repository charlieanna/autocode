"""Route discovery (#1426 steps 4-5): what the tool's connected accounts offer.

Reads the tool's own local listings — its model list and its login list — and turns
them into the candidate dicts the selector consumes (``autocode_route_selector``).
A local listing only: discovery never launches a model, never touches a credential,
and never connects an account. Anything a listing cannot say stays unknown: a model's
reasoning levels are ``None`` unless a tool reports them, and a model no connected
login can bill is not a candidate at all, because its billing route would be
unverified (#888's rule).

Two producers, one shape. A configured tool (``providers/command.py``, such as Kilo
Code) declares its billing routes in ``[auth]``: each route names the model prefix it
serves, the login name the tool prints, how that login bills, and the pattern with the
mode that proves the login is connected. OpenCode has no such table, so its logins map
to model prefixes here (``OPENCODE_ROUTES``), from the prefixes its own listing serves
per login; the free ``opencode/`` tier needs no login and is always a candidate.

Billing classification follows the plans the logins actually are: the coding and token
plans meter a subscription, the Kilo Gateway and API keys bill per token, and OpenAI's
one login bills either way depending on its mode. A login nothing declares — a vendor
OpenCode adds next week — is ignored until a route names it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

try:
    from . import autocode_route_levels as route_levels
except ImportError:
    import autocode_route_levels as route_levels

try:
    from providers import env_prep, opencode
except ImportError:
    from .providers import env_prep, opencode

# OpenCode login name -> (model prefixes it serves, billing). OpenAI's billing follows
# its mode: oauth is the ChatGPT subscription, api is a per-token key.
OPENCODE_ROUTES = {
    "OpenAI": ("openai/", "by-mode"),
    "Z.AI Coding Plan": ("zai-coding-plan/", "subscription"),
    "Zhipu AI Coding Plan": ("zhipuai-coding-plan/", "subscription"),
    "Xiaomi Token Plan (Singapore)": ("xiaomi-token-plan-sgp/ mimo-token-plan/", "subscription"),
    "Alibaba Token Plan": ("alibaba-token-plan/", "subscription"),
    "Anthropic": ("anthropic/", "pay per token"),
}
# OpenCode's free tier serves these with no login at all; they bill nothing until used
# past their limits, so discovery records them as the Zen catalogue they are.
OPENCODE_FREE_PREFIX = "opencode/"
OPENCODE_FREE_ACCOUNT = "OpenCode Zen"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def discover(provider, workspace=None, *, env=None, inventory=None) -> dict:
    """The candidates and accounts ``provider`` offers: ``{tool, candidates, accounts}``.

    ``candidates`` are the selector's dicts (model, tool, account, billing, levels,
    probe, metadata); ``metadata`` is the model's listed family, tool-calling and
    reasoning support, context and release date (``autocode_route_levels.metadata``), or
    None when the tool prints no verbose listing (#2049); ``accounts`` names each connected login with its mode, billing and the
    model prefixes it serves. A listing that cannot be read is an error, phrased like
    the provider layer's own, because choosing routes blind would be worse than
    stopping.
    """
    if callable(getattr(provider, "auth_table", None)):
        return _from_command_provider(provider, workspace, env, inventory)
    if callable(getattr(provider, "auth_logins", None)):
        return _from_opencode(workspace, env, inventory)
    name = getattr(provider, "NAME", "the provider")
    raise RuntimeError(f"Cannot discover models for {name}; it lists neither routes nor logins")


def candidates(provider, workspace=None, *, env=None) -> list:
    """Just the candidate list, for the wiring that feeds the selector."""
    return discover(provider, workspace, env=env)["candidates"]


def _from_command_provider(provider, workspace, env, inventory=None):
    table = provider.auth_table()
    if not table["command"] or not table["routes"]:
        return {"tool": table["name"], "candidates": [], "accounts": []}
    summary = _run(table["command"], table["name"], workspace, env, table["timeout_seconds"])
    connected = []
    for route in table["routes"]:
        found = re.findall(route["pattern"], summary, re.MULTILINE)
        if found and route["expect"] in found:
            connected.append(route)
    models = _models(provider, workspace, env, inventory)
    return {
        "tool": table["name"],
        "candidates": _candidates(models, table["name"], connected, *_listing(provider, workspace, env)),
        "accounts": [
            {
                "name": route["name"],
                "mode": route["expect"],
                "billing": route.get("billing"),
                "prefixes": [route["models"]],
            }
            for route in connected
        ],
    }


def _from_opencode(workspace, env, inventory=None):
    logins = opencode.auth_logins(workspace, env=env)
    if logins is None:
        raise RuntimeError("Cannot read OpenCode's logins; run `opencode auth list`; no agent was launched")
    modes = dict(logins)
    routes = []
    for name, mode in logins:
        declared = OPENCODE_ROUTES.get(name)
        if declared is None:
            continue
        prefixes, billing = declared
        if billing == "by-mode":
            billing = {"oauth": "subscription", "api": "pay per token"}.get(mode)
        routes.extend({"models": prefix, "name": name, "billing": billing} for prefix in prefixes.split())
    # The free tier needs no login, so it is a route every listing serves.
    routes.append({"models": OPENCODE_FREE_PREFIX, "name": OPENCODE_FREE_ACCOUNT, "billing": "pay per token"})
    models = _models(opencode, workspace, env, inventory)
    accounts = {}
    for route in routes:
        account = accounts.setdefault(
            route["name"],
            {
                "name": route["name"],
                "mode": modes.get(route["name"], "none"),
                "billing": route["billing"],
                "prefixes": [],
            },
        )
        if route["models"] not in account["prefixes"]:
            account["prefixes"].append(route["models"])
    return {
        "tool": "opencode",
        "candidates": _candidates(models, "opencode", routes, *_listing(opencode, workspace, env)),
        "accounts": [accounts[name] for name in sorted(accounts)],
    }


def _candidates(models, tool, routes, levels=None, metadata=None):
    """One candidate per model a connected route serves, in a stable order."""
    found = []
    for model in sorted(models or []):
        for route in routes:
            if model.startswith(route["models"]):
                found.append(
                    {
                        "model": model,
                        "tool": tool,
                        "account": route["name"],
                        "billing": route.get("billing"),
                        "levels": (levels or {}).get(model),
                        "probe": None,
                        "metadata": (metadata or {}).get(model),
                    }
                )
                break
    return found


def _listing(provider, workspace, env):
    """``(levels, metadata)`` from one verbose model listing; both empty when it cannot be read."""
    # Only tools with this documented local-listing format. A custom provider's
    # arbitrary models command must never be rewritten or treated as a model call.
    name = getattr(provider, "NAME", "opencode" if provider is opencode else None)
    config = getattr(provider, "_config", {})
    # A connection whose command runs kilo (a copy of kilocode.toml with other roles) lists its
    # variants the same way as the bundled one (#2006, #2010); only the documented listing is run.
    words = config.get("command") if isinstance(config, dict) else None
    runs_kilo = isinstance(words, list) and bool(words) and isinstance(words[0], str) and Path(words[0]).name == "kilo"
    command = config.get("models_command") if name == "kilocode" or runs_kilo else None
    if provider is opencode:
        command = ["opencode", "models"]
    if command not in (["opencode", "models"], ["kilo", "models"]):
        return {}, {}
    try:
        listing = _run([*command, "--verbose"], name, workspace, env, 30)
    except RuntimeError:
        # Older tools may lack verbose metadata. Qualification evidence remains
        # the fallback; an unreadable optional listing never invents new levels.
        return {}, {}
    return route_levels.parse(listing), route_levels.metadata(listing)


def _models(provider, workspace, env, inventory=None):
    listed = (
        inventory.available_models(workspace)
        if inventory is not None
        else provider.available_models(workspace, env=env)
    )
    if listed is None:
        raise RuntimeError("Cannot read the tool's model listing; automatic selection cannot guess availability")
    return listed


def _run(command, name, workspace, env, timeout_seconds):
    try:
        result = env_prep.preflight_run(
            command,
            env_prep.snapshot_environment(env),
            cwd=workspace,
            require_executable=env is not None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"cannot verify {name} login; auth listing timed out after {timeout_seconds} seconds; "
            "no provider request was launched"
        ) from None
    except OSError:
        raise RuntimeError(
            f"cannot verify {name} login; auth listing could not start; no provider request was launched"
        ) from None
    if result.returncode:
        raise RuntimeError(
            f"cannot verify {name} login; auth listing failed (exit {result.returncode}); "
            "no provider request was launched"
        ) from None
    return _ANSI.sub("", result.stdout + result.stderr)
