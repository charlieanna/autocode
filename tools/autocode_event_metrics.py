"""Pure aggregation of normalized provider usage events; no runner imports."""


def summarize(rows, request_context, reported_cost):
    completed = [r for r in rows if r.get("type") == "turn.completed" and isinstance(r.get("usage"), dict)]
    usages = [
        r["usage"]
        for r in rows
        if r.get("type") in ("turn.completed", "turn.failed", "usage.partial") and isinstance(r.get("usage"), dict)
    ]
    keys = ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]
    usage = {k: sum(u[k] for u in usages) if usages and all(k in u for u in usages) else None for k in keys}
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
    return {
        "provider_tokens": usage,
        "request_context": request_context,
        "provider_tokens_partial": any(r.get("type") == "usage.partial" for r in rows),
        "provider_requests": None,
        "provider_retries": None,
        "completed_turns": len(completed),
        "headroom_transformed": None,
        "provider_cost_usd": reported_cost,
        "cost_state": cost_states or None,
        "provider_sessions": sorted(sessions) or None,
    }
