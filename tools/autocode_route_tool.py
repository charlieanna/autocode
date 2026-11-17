"""Move one job to another connected tool when its quota runs out (owner decision 2026-11-16).

A run's tools are fixed when it starts (``autocode_role_transport.guard``). When a job's quota runs
out, the model question (``autocode_quota_route``) may be answered with a model on another connected
tool, ``--answer route-ROLE=TOOL:MODEL``: Anthropic API credit ran out on Kilo while the same models
were available through a Claude Code subscription, and the run could only stop. ``prepare`` checks
that answer the way ``--role-transport`` checks a tool at launch; the route assignment then saves the
binding (``role_transport.rebind``). Every other stop, and every other job, keeps its tool.

Imports no runner module: the run's own tool arrives as ``fallback``.
"""

from __future__ import annotations

try:
    from . import autocode_providers as providers
    from . import autocode_quota_route as quota_route
except ImportError:
    import autocode_providers as providers
    import autocode_quota_route as quota_route


def current_tool(settings: dict, role: str) -> str:
    """The tool ``role`` runs on now: its own binding, else the run's."""
    return ((settings.get("roles") or {}).get(role) or {}).get("provider") or settings.get("provider") or "opencode"


def prepare(settings: dict, role: str, tool: str, model: str, origin: dict, fallback, workspace) -> dict | None:
    """``{"binding", "configured"}`` for moving ``role`` to ``tool`` on ``model``, or None for its own tool.

    Raises ValueError for any answer a launch would refuse: not a quota stop, not on the facade
    engine, a tool that is not connected, or a model that tool does not list or cannot bill.
    """
    if tool == current_tool(settings, role):
        return None
    if (origin or {}).get("pause_status") != quota_route.QUOTA_STATUS:
        raise ValueError("Only a quota stop moves a job to another tool; name a model for its own tool")
    if quota_route.engine(settings, role) != "opencode":
        raise ValueError("Only a run on a connected tool can move a job to another one; Codex keeps its own")
    return bind(settings, role, settings["roles"][role], tool, model, fallback, workspace)


def bind(settings: dict, role: str, route: dict, tool: str, model: str, fallback, workspace) -> dict:
    """``{"binding", "configured"}`` for running ``role``'s ``route`` on ``tool`` with ``model``, checked as a launch is.

    The one check behind every move of a job to another tool: a quota answer (``prepare``) and a pinned
    Investigator (``--investigator-model TOOL:MODEL``). Raises ValueError for a run off the facade engine,
    a tool that is not connected, or a model that tool does not list or cannot bill."""
    if settings.get("engine") != "opencode":
        raise ValueError("Only a run on a connected tool can move a job to another one; Codex keeps its own")
    own = settings.get("provider") or "opencode"
    try:
        facade = fallback if tool == own and fallback is not None else providers.resolve(tool)
    except (ValueError, RuntimeError, OSError) as error:
        raise ValueError(f"{tool} is not a connected tool: {error}") from None
    configured = bool(getattr(facade, "CONFIGURED", False))
    if tool != own and not configured:
        raise ValueError(
            f"{tool} is not a connected tool with its own sign-in; name a tool from `autocode connections`"
        )
    route = {**route, "engine": "opencode", "provider": tool, "model": model}
    try:
        facade.check_models({role: route}, workspace)
        facade.check_subscription_routes({role: route}, workspace)
        identity = facade.local_settings(workspace) if tool != own else None
    except RuntimeError as error:
        raise ValueError(str(error)) from None
    return {"binding": {"provider": tool, "identity": identity}, "configured": configured}
