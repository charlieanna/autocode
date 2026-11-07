"""Deliver classified source corrections through the public intervention ledger."""

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_conversation as policy
    from . import autocode_coordination_conversation_runtime as runtime
    from . import autocode_coordination_store as store
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_conversation as policy
    import autocode_coordination_conversation_runtime as runtime
    import autocode_coordination_store as store
    import autocode_util as util


def deliver(document, member, message, run):
    """Save and consume one idempotent correction, without launching its refreshed plan."""
    pending = document.get("pending") or {}
    if pending and not pending.get("request_id"):
        # Older versions submitted ordinary feedback. Only its actual source
        # record proves application; a conversation echo does not.
        fresh = run.status()
        if not any(row.get("text") == message for row in fresh.get("request_sources", [])):
            raise store.CoordinationError("Legacy request feedback has no confirmed source; it was not submitted twice")
        member["brief"] = message
        document["pending"] = None
        store.save(document)
        return
    identifier = pending.get("request_id") or "request-refresh-" + util.digest(
        [document["id"], member["run_dir"], message]
    )
    document["pending"] = {
        "operation": "request_feedback",
        "target": member["target"],
        "message": message,
        "request_id": identifier,
    }
    store.save(document)
    expected = {"id": identifier, "kind": "feedback", "text": message}
    runtime.submit(
        run,
        [
            "intervention",
            "submit",
            "--workspace",
            str(run.workspace),
            "--run-dir",
            str(run.run_dir),
            "--request-id",
            identifier,
            "--kind",
            "feedback",
            "--text",
            message,
            "--json",
        ],
        expected,
    )
    fresh = runtime.observation(run)
    if identifier in (fresh.get("interventions") or {}).get("pending_ids", []):
        run.advance()  # inbox consumption pauses before any provider launch
        fresh = runtime.observation(run)
    receipts = (fresh.get("interventions") or {}).get("applied_receipts", [])
    if not any(all(row.get(key) == value for key, value in expected.items()) for row in receipts) or not any(
        row.get("text") == message for row in fresh.get("request_sources", [])
    ):
        raise store.CoordinationError("Request feedback has no confirmed applied source receipt")
    member["brief"] = message
    history = document.setdefault("request_recovery", [])
    if not any(row.get("request_id") == identifier for row in history):
        history.append({"at": util.now(), "target": member["target"], "request_id": identifier})
    document["pending"] = None
    store.save(document)


def resume_step(document, views, *, run_for):
    """Offer a budgeted resume only for the coordinator's own correction hold."""
    for member in document["members"]:
        target = member["target"]
        owned = {
            row["request_id"]
            for row in document.get("request_recovery", [])
            if row.get("target") == target and row.get("request_id")
        }
        if not owned or (views.get(target) or {}).get("status") != "PAUSED_INTERVENTION":
            continue
        fresh = runtime.observation(run_for(member))
        if policy.resumable(fresh, owned):
            return {"kind": "request_resume", "target": target}
        return {"kind": "blocked", "reason": "Request recovery cannot release a separate or uncertain participant hold"}
    return None


def resume(document, member, run, limits):
    """Recheck the public owned-pause action immediately before resuming work."""
    owned = {
        row["request_id"]
        for row in document.get("request_recovery", [])
        if row.get("target") == member["target"] and row.get("request_id")
    }
    fresh = runtime.observation(run)
    if not policy.resumable(fresh, owned):
        raise store.CoordinationError("Request recovery no longer has an exclusively owned pause")
    named = actions.named(fresh, run.workspace, run.run_dir)
    if named.argv != ["--resume-paused"] or named.env:
        raise store.CoordinationError("Request recovery names a different resume action")
    run.options = limits
    run.resume_paused()
