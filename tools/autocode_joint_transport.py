"""Check saved joint-planning transports without importing the controller."""


def check(state, workspace, *, engine_for, support, opencode, qwen, check_subscription):
    identities = state["settings"]["transport_identities"]
    if "gocode" in identities:
        raise support.Paused("PAUSED_TRANSPORT_CHANGED", "This saved run used the gocode engine, which this "
                             "checkout no longer bundles; resume it from a checkout that has it, or start a "
                             "new run with --provider gocode and a user-level provider config")
    roles = {role: config for role, config in state["settings"]["roles"].items()
             if engine_for(state["settings"], role) == "codex"}
    codex_changed = False
    if roles:
        codex = support.local_settings()
        check_subscription(codex)
        codex_changed = support.transport_drift(codex, identities["codex"], roles)
    opencode_roles = {role: config for role, config in state["settings"]["roles"].items()
                      if engine_for(state["settings"], role) == "opencode"}
    opencode_changed = False
    if opencode_roles:
        try:
            opencode.check_subscription_routes(opencode_roles, workspace)
        except RuntimeError as error:
            raise support.Paused("PAUSED_BILLING_ROUTE", str(error)) from error
        opencode_changed = opencode.transport_drift(opencode.local_settings(workspace), identities["opencode"])
    qwen_roles = {role: config for role, config in state["settings"]["roles"].items()
                  if engine_for(state["settings"], role) == "qwen"}
    qwen_changed = False
    if qwen_roles:
        qwen_changed = qwen.transport_drift(qwen.local_settings(workspace), identities["qwen"])
    if codex_changed or opencode_changed or qwen_changed:
        raise support.Paused("PAUSED_TRANSPORT_CHANGED", "A joint-planning CLI/auth/provider configuration changed")
