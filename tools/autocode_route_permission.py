"""Keep automatic escalation inside the connected routes saved at launch."""


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
