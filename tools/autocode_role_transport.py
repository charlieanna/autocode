"""Explicit new-run transport selection; immutable role facades, no controller imports.

configure writes only existing settings.roles[].engine/provider/model and
settings.transport_identities["role:<role>"]. validate/entry, configured, groups,
adapter, report_reader and session_adapter read those bindings. The existing
controller owns sessions per role; this module never writes a shared session.
No new top-level run-state key or alternative screen/role naming table is added.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import re

try:
    from . import autocode_providers as providers
    from . import autocode_util as util
except ImportError:
    import autocode_providers as providers
    import autocode_util as util


PREFIX = "role:"


def resume_session(state: RunState, role, stage, joint, report_only, supports, requested=None):
    """Only conversational Requirements may reuse a joint-planning session."""
    conversational = stage == "requirements_gather" and (state.get("settings") or {}).get("spec_first")
    if requested:
        return requested
    if not supports or report_only or (joint and not conversational):
        return None
    return (state.get("sessions") or {}).get(role)


DERIVED_ROLES: dict[str, tuple[str, ...]] = {
    "analyst": ("plan_reviewer", "astra"),
    "investigator": ("plan_reviewer", "astra"),
    "architect": ("plan_reviewer", "astra"),
    "acceptance_tester": ("sol",),
    "resolver": ("astra",),
    "stuck_investigator": ("plan_reviewer", "astra"),
    "utility": ("plan_reviewer", "requirements", "astra"),
}


def inherit(settings, role, source_role=None):
    """Save an immutable tool identity for a derived role copied mid-run from a source role."""
    if not settings or not isinstance(settings, dict):
        return None
    identities = settings.get("transport_identities")
    if not identities or not isinstance(identities, dict):
        return None
    target_key = PREFIX + str(role)
    if target_key in identities:
        return identities[target_key]

    routes = settings.get("roles", {})
    route = routes.get(role, {})
    provider = route.get("provider")
    if provider in (None, "opencode", "openai", settings.get("provider")):
        # The role runs on the run's own tool, so there is no external identity to copy:
        # a copied one would contradict its route, and `_entry` refuses that pairing.
        return None

    sources = (source_role,) if source_role else DERIVED_ROLES.get(role, ())
    for src in sources:
        if not src:
            continue
        src_key = PREFIX + str(src)
        src_identity = identities.get(src_key)
        if src_identity is not None and isinstance(src_identity, dict) and src_identity.get("provider") == provider:
            inherited = copy.deepcopy(src_identity)
            settings.setdefault("transport_identities", {})[target_key] = inherited
            return inherited

    for k, v in identities.items():
        if k.startswith(PREFIX) and isinstance(v, dict) and v.get("provider") == provider:
            inherited = copy.deepcopy(v)
            settings.setdefault("transport_identities", {})[target_key] = inherited
            return inherited

    return None


def add_arguments(parser):
    """The two transport inputs; keep the provider flag's existing semantics."""
    parser.add_argument(
        "--provider",
        default=None,
        help="Tool that runs each role for a new run. Default: AUTOCODE_PROVIDER, then default_provider in "
        "~/.config/autocode/config.toml, then opencode. Other names load ~/.config/autocode/providers/<name>.toml",
    )
    parser.add_argument(
        "--role-transport",
        action="append",
        default=[],
        metavar="ROLE=PROVIDER",
        help="New runs only: select an external CLI tool for this role; other roles keep their saved model routes",
    )


def engine_provider(args, parser) -> None:
    """Reconcile ``--engine`` with ``--provider`` before a run starts.

    Claude Code is the built-in claude provider on the facade engine (#717). Codex is an engine, not a
    provider config: ``--provider codex`` used to fail only after the worktree was created, asking for a
    TOML file for a tool AutoCode already runs (#2330).
    """
    if args.engine == "claude":
        if args.provider not in (None, "claude"):
            parser.error(f"engine claude runs the built-in claude provider; it cannot run --provider {args.provider}")
        args.engine, args.provider = "opencode", "claude"
    elif args.provider == "codex":
        parser.error("codex is an engine, not a provider config: run with --engine codex and no --provider")


def guard(args, state: RunState):
    """Reject a saved binding change before the ordinary configure path mutates anything."""
    settings = state.get("settings")
    selected = getattr(args, "role_transport", None)
    if selected and (settings or state.get("sessions") or state.get("history")):
        raise ValueError("--role-transport is only available when starting a new run; saved tools are immutable")
    if selected and getattr(args, "replay_recording", None):
        raise ValueError("--role-transport cannot combine with recorded replay")
    if enabled(settings) and getattr(args, "accept_transport_change", False):
        raise ValueError("Mixed-role tool identities are immutable; start a new run after a transport change")


def enabled(settings):
    validate(settings)
    return any(key.startswith(PREFIX) for key in (settings or {}).get("transport_identities", {}))


def _entry(settings, role):
    settings = settings or {}
    identities = settings.get("transport_identities", {})
    route = settings.get("roles", {}).get(role, {})
    key = PREFIX + str(role)
    selected = identities.get(key)
    if selected is None:
        selected = inherit(settings, role)
    if selected is None:
        external_route = (route.get("engine") or settings.get("engine")) == "opencode" and route.get(
            "provider"
        ) not in (
            None,
            "opencode",
            "openai",
            settings.get("provider"),
        )
        if key in identities or external_route:
            raise ValueError(f"Saved external role transport identity is missing for {role}; no fallback")
        return None
    if (
        not isinstance(selected, dict)
        or set(selected) != {"provider", "identity"}
        or not isinstance(selected["provider"], str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", selected["provider"])
        or selected["provider"] in ("opencode", "openai")
        or not valid_identity(selected["identity"], selected["provider"])
        or route.get("provider") != selected["provider"]
        or route.get("engine", settings.get("engine")) != "opencode"
    ):
        raise ValueError("Saved role transport does not match its immutable route")
    return selected


def valid_identity(identity, provider):
    """The configured command facade's existing identity contract, not model authority."""
    return (
        isinstance(identity, dict)
        and identity.get("engine") == provider
        and type(identity.get("identity_version")) is int
        and identity["identity_version"] == 1
        and isinstance(identity.get("executable"), str)
        and bool(identity["executable"])
        and isinstance(identity.get("config_path"), str)
        and bool(identity["config_path"])
        and isinstance(identity.get("config_sha256"), str)
        and len(identity["config_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in identity["config_sha256"])
        and (identity.get("version") is None or isinstance(identity["version"], str))
    )


def validate(settings):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("Saved role transport settings are malformed")
    identities = settings.get("transport_identities", {})
    routes = settings.get("roles", {})
    if not isinstance(identities, dict) or not isinstance(routes, dict):
        raise ValueError("Saved role transport record is malformed")
    for key in identities:
        if not isinstance(key, str):
            raise ValueError("Saved role transport identity name is malformed")
        if key.startswith(PREFIX) and key[len(PREFIX) :] not in routes:
            raise ValueError("Saved role transport names an unknown role")
    for role, route in routes.items():
        if not isinstance(route, dict):
            raise ValueError("Saved role route is malformed")
        _entry(settings, role)


def entry(settings, role):
    # Validate every selection before choosing one, even when its own marker is absent.
    validate(settings)
    return _entry(settings, role)


def configured(settings, role, fallback=False):
    # Overrides are configured providers; legacy Codex model_provider flags are unrelated.
    return entry(settings, role) is not None or fallback


def configure(settings, args, workspace):
    requested = getattr(args, "role_transport", None) or ()
    if not requested:
        return
    if settings.get("engine") != "opencode" or settings.get("provider") not in (None, "opencode"):
        raise ValueError("--role-transport phase 1 requires --engine opencode --provider opencode")
    if settings.get("replay_recording") or getattr(args, "single_model", None):
        raise ValueError("--role-transport cannot combine with replay or --single-model")
    parsed = {}
    for item in requested:
        if not isinstance(item, str) or item.count("=") != 1:
            raise ValueError("--role-transport requires ROLE=PROVIDER")
        role, name = item.split("=", 1)
        if role not in settings["roles"] or role in parsed:
            raise ValueError("--role-transport needs an existing role named exactly once")
        if not getattr(args, f"{role}_model", None):
            raise ValueError(f"--role-transport {role} requires an explicit --{role.replace('_', '-')}-model")
        parsed[role] = name
    # Validate all selections before changing any route. Never infer a provider from a model name.
    bindings = {}
    for role, name in parsed.items():
        facade = providers.resolve(name)
        if not getattr(facade, "CONFIGURED", False):
            raise ValueError("--role-transport selects an external configured tool; builtin roles keep their route")
        existing = entry(settings, role)
        if existing is not None:
            if existing["provider"] != name:
                raise ValueError("Role transport is already bound")
            continue
        explicit_model = getattr(args, f"{role}_model")
        route = {**settings["roles"][role], "engine": "opencode", "provider": name, "model": explicit_model}
        facade.check_models({role: route}, workspace)
        facade.check_subscription_routes({role: route}, workspace)
        bindings[role] = (route, {"provider": name, "identity": copy.deepcopy(facade.local_settings(workspace))})
    for role, (route, binding) in bindings.items():
        settings["roles"][role].update(engine="opencode", provider=binding["provider"], model=route["model"])
        settings.setdefault("transport_identities", {})[PREFIX + role] = binding


def adapter(settings, role, fallback, workspace=None):
    selected = entry(settings, role)
    if selected is None:
        return fallback
    facade = providers.resolve(selected["provider"])
    if not getattr(facade, "CONFIGURED", False):
        raise ValueError("Saved role transport requires its configured tool; no fallback")
    if workspace is not None and facade.transport_drift(facade.local_settings(workspace), selected["identity"]):
        raise util.Paused("PAUSED_TRANSPORT_CHANGED", f"The saved {role} tool identity changed; no fallback")
    return facade


def groups(settings, fallback, workspace=None):
    """Yield separate role sets; an external catalogue never chooses an OpenCode role."""
    validate(settings)
    default_roles = {}
    for role, route in settings.get("roles", {}).items():
        if route.get("engine", settings.get("engine")) != "opencode":
            continue
        selected = entry(settings, role)
        if selected is None:
            default_roles[role] = route
        else:
            yield adapter(settings, role, fallback, workspace), {role: route}, selected["identity"]
    if default_roles:
        identities = settings.get("transport_identities", {})
        yield fallback, default_roles, identities.get("opencode", settings.get("transport_identity"))


def check_models(settings, fallback, workspace, inventory=None):
    """Validate admission in each concrete catalogue; the invocation cache owns only its default."""
    for facade, routes, _identity in groups(settings, fallback, workspace):
        if inventory is not None and facade is fallback:
            inventory.check_models(routes)
        else:
            facade.check_models(routes, workspace)


def check_subscription_routes(settings, fallback, workspace):
    """Check each concrete tool's auth route without widening its model authority."""
    for facade, routes, _identity in groups(settings, fallback, workspace):
        facade.check_subscription_routes(routes, workspace)


def report_reader(state: RunState | None, record, fallback, workspace):
    """Use the completed stage's saved role, including during public reconciliation."""
    settings = (state or {}).get("settings")
    role = record.get("route_role", record.get("role"))
    return adapter(settings, role, fallback, workspace).final_report


def session_adapter(state: RunState, role, fallback, workspace):
    """Resolve the immutable stage facade and its own session capability together."""
    facade = adapter(state["settings"], role, fallback, workspace)
    return facade, getattr(facade, "SUPPORTS_SESSIONS", True)
