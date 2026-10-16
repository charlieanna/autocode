"""Check saved joint-planning transports without importing the controller."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_role_transport as role_transport
except ImportError:
    import autocode_role_transport as role_transport


def check(state: RunState, workspace, *, engine_for, support, opencode, check_subscription):
    identities = state["settings"]["transport_identities"]
    retired = next((name for name in ("gocode", "qwen") if name in identities), None)
    if retired:
        raise support.Paused(
            "PAUSED_TRANSPORT_CHANGED",
            f"This saved run used the {retired} engine, which this "
            "checkout no longer bundles; resume it from a checkout that has it, or start a "
            f"new run with --provider {retired} and a user-level provider config",
        )
    roles = {
        role: config
        for role, config in state["settings"]["roles"].items()
        if engine_for(state["settings"], role) == "codex"
    }
    codex_changed = False
    if roles:
        codex = support.local_settings()
        check_subscription(codex)
        codex_changed = support.transport_drift(codex, identities["codex"], roles)
    opencode_roles = {
        role: config
        for role, config in state["settings"]["roles"].items()
        if engine_for(state["settings"], role) == "opencode"
    }
    opencode_changed = False
    if opencode_roles:
        for facade, routed, saved in role_transport.groups(state["settings"], opencode, workspace):
            try:
                facade.check_subscription_routes(routed, workspace)
            except RuntimeError as error:
                raise support.Paused("PAUSED_BILLING_ROUTE", str(error)) from error
            changed = facade.transport_drift(facade.local_settings(workspace), saved)
            opencode_changed = changed or opencode_changed
    if codex_changed or opencode_changed:
        raise support.Paused("PAUSED_TRANSPORT_CHANGED", "A joint-planning CLI/auth/provider configuration changed")
