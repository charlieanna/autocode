"""Keep automatic escalation inside the connected routes saved at launch."""

try:
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_verifier_independence as independence


def allows(settings, role, model, effort):
    """Legacy runs keep their policy; automatic runs may only use saved accounts.

    Existing retry/ladder policies decide strength. This guard decides permission,
    including an advertised reasoning-level veto, without rediscovering accounts.
    """
    selection = (settings or {}).get("route_selection")
    if not selection:
        return True
    candidates = [row for rows in (selection.get("candidates") or {}).values() for row in rows]
    for row in candidates:
        if row.get("model") != model:
            continue
        levels = row.get("levels")
        if effort is None or levels is None or effort in levels:
            return True
    return False


def investigator(settings, stuck_model):
    """An independent already-permitted diagnostic route and its inheritance source.

    Reuse the run's current reviewer first (including a recovered replacement),
    never the legacy paid-gateway default. Keep its tool and supported level.
    Astra remains reserved for the Resolver, as in the fixed-route policy.
    """
    roles = settings.get("roles") or {}
    vendor = independence.same_vendor_engine(settings)
    for source in ("plan_reviewer", "sol", "glm", "completion", "requirements"):
        route = roles.get(source) or {}
        model = route.get("model")
        if not model or "astra" in model.lower() or independence.may_share(model, stuck_model, vendor_engine=vendor):
            continue
        if allows(settings, source, model, route.get("reasoning_effort")):
            return source, dict(route)
    return None
