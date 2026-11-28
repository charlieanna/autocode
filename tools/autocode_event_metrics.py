"""Pure aggregation of normalized provider usage events; no runner imports."""


def receipts(rows):
    """Each provider request id once, with the model the provider resolved for it (#4239).

    Only what a translator recorded: no id is invented, a model the provider did not name stays
    None, and two different models named for one id leave it None rather than choosing one.
    """
    models, conflicts = {}, set()
    for row in rows:
        listed = row.get("provider_requests")
        for item in listed if isinstance(listed, list) else ():
            key = item.get("id") if isinstance(item, dict) else None
            if not isinstance(key, str) or not key:
                continue
            model = item.get("model") if isinstance(item.get("model"), str) and item.get("model") else None
            if key not in models:
                models[key] = model
            elif model and models[key] and models[key] != model:
                conflicts.add(key)
            elif model and models[key] is None:
                models[key] = model
    return [{"id": key, "model": None if key in conflicts else model} for key, model in models.items()] or None


def summarize(rows, request_context, reported_cost):
    completed = [r for r in rows if r.get("type") == "turn.completed" and isinstance(r.get("usage"), dict)]
    usages = [
        r["usage"]
        for r in rows
        if r.get("type") in ("turn.completed", "turn.failed", "usage.partial") and isinstance(r.get("usage"), dict)
    ]
    keys = ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]
    usage = {k: sum(u[k] for u in usages) if usages and all(k in u for u in usages) else None for k in keys}
    # Subsets of input that only some translators report (Claude Code): kept when every usage row has
    # them, never zero-filled, so an engine that does not report them stays unknown.
    for k in ("cache_write_tokens", "fresh_input_tokens"):
        if usages and all(type(u.get(k)) is int for u in usages):
            usage[k] = sum(u[k] for u in usages)
    # The CLI's saved cost-state records (#2592) and the sessions this provider activity
    # belongs to, so an amount can bind to the exact attempt that earned it. The thread's own
    # id names the session even when a failure stripped its diagnostics (#2599 review).
    cost_states = [r["cost_state"] for r in rows if isinstance(r.get("cost_state"), dict)]
    sessions = {
        session
        for r in rows
        for session in (
            r.get("session_id"),
            r.get("sessionID"),
            (r.get("claude") or {}).get("session_id"),
            r.get("thread_id") if r.get("type") == "thread.started" else None,
        )
        if isinstance(session, str)
    }
    first_request = next(
        (r["first_request_usage"] for r in rows if isinstance(r.get("first_request_usage"), dict)),
        None,
    )
    return {
        "provider_tokens": usage,
        "first_request_tokens": first_request,
        "request_context": request_context,
        "provider_tokens_partial": any(r.get("type") == "usage.partial" for r in rows),
        "provider_requests": None,
        "provider_retries": None,
        "completed_turns": len(completed),
        "headroom_transformed": None,
        "provider_cost_usd": reported_cost,
        "cost_state": cost_states or None,
        "provider_sessions": sorted(sessions) or None,
        "provider_receipts": receipts(rows),
    }
