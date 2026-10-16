"""The utility route: a cheaper model for stages that only classify or reformat (#710).

``--utility-model MODEL`` saves ``settings["roles"]["utility"]`` on a new run, and the stages
listed in ``autocode_roles.UTILITY_STAGES`` then launch on it (``units.autoplanner.route_for``
asks ``route``). The route copies the Plan Reviewer's engine, provider and reasoning effort
(the Recognizer's own route on a run without one) and replaces only the model, so a run
without the flag saves no utility route and every stage keeps the route it had before.

A stage that writes code or judges evidence is never on the list (autocode_roles says why),
so a weak or local model here can misroute a request but never accept or produce work.
State key written here: settings.roles.utility (read by run_role through route_for).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
except ImportError:
    import autocode_role_transport as role_transport
    import autocode_roles as roles

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def add_arguments(parser) -> None:
    stages = ", ".join(roles.UTILITY_STAGES)
    parser.add_argument(
        "--utility-model",
        help="New runs: a cheaper model for the stages that only classify or reformat "
        f"({stages}); it keeps the Plan Reviewer's engine and provider. Never used for a stage "
        "that writes code or judges evidence. Default: each stage keeps its own route",
    )
    parser.add_argument(
        "--utility-reasoning-effort",
        choices=EFFORTS,
        help="Reasoning effort for --utility-model (default: the Plan Reviewer's)",
    )


def configure(settings: dict, args, *, started: bool) -> str | None:
    """Save the utility route a new run asked for. Returns the role name to validate, or None.

    Raises ValueError for a saved run (the route is a new-run choice: recognition has already
    run) and for an effort without a model.
    """
    model = getattr(args, "utility_model", None)
    effort = getattr(args, "utility_reasoning_effort", None)
    if not model and not effort:
        return None
    if started:
        raise ValueError("--utility-model is only available when starting a new run")
    if not model:
        raise ValueError("--utility-reasoning-effort needs --utility-model")
    saved = settings.setdefault("roles", {})
    source = "plan_reviewer" if "plan_reviewer" in saved else ("requirements" if "requirements" in saved else "astra")
    base = saved.get(source) or {}
    route = {key: base.get(key) for key in ("engine", "provider", "reasoning_effort") if key in base}
    route.update(model=model)
    if effort:
        route["reasoning_effort"] = effort
    saved[roles.UTILITY_ROUTE] = route
    role_transport.inherit(settings, roles.UTILITY_ROUTE, source)
    return roles.UTILITY_ROUTE


def route(state: Mapping[str, Any], stage: str) -> str | None:
    """The utility route's name when ``stage`` is a utility stage and the run saved one, else None."""
    if roles.base_stage(stage) not in roles.UTILITY_STAGES:
        return None
    saved = ((state.get("settings") or {}).get("roles")) or {}
    return roles.UTILITY_ROUTE if roles.UTILITY_ROUTE in saved else None
