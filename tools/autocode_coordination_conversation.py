"""Task-level conversation policy over public participant observations.

Questions preserve task authority. Explicit changes retain the original human
text and use ordinary feedback/review; model-selected targets are advisory.
"""

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_store as store
    from . import autocode_message_context as message_context
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_store as store
    import autocode_message_context as message_context
    import autocode_util as util


def request_id(document, message, target, kind):
    """Bind idempotent public actions to one task message and participant."""
    return "task-message-" + util.digest([document["id"], document["anchor"], message["id"], target, kind])


def resumable(view, owned):
    """Never release somebody else's hold, even if a newer pause hid its intent."""
    intervention = view.get("interventions") or {}
    intent = intervention.get("pause_intent") or {}
    receipts = intervention.get("applied_receipts") or []
    outstanding = [row for row in receipts if row.get("kind") in ("pause", "feedback") and not row.get("resumed_at")]
    return bool(
        view.get("status") == "PAUSED_INTERVENTION"
        and (view.get("needs") or {}).get("kind") == "resume"
        and (view.get("recovery") or {}).get("cause") == "PAUSED_INTERVENTION"
        and not actions.working(view)
        and not any(row.get("active") for row in (view.get("usage") or {}).get("accounting", {}).get("attempts", []))
        and not intervention.get("pending_count")
        and not intervention.get("pending_ids")
        and not intervention.get("inbox_error")
        and not intervention.get("stop_intent")
        and not any(row.get("kind") == "stop" for row in receipts)
        and not intent.get("held_pause")
        and outstanding
        and all(row.get("id") in owned for row in outstanding)
        and all(identifier in owned for identifier in intent.get("request_ids") or [])
    )


def context(document, views):
    """Copy the bounded public member plans; never substitute an unread status."""
    rows = []
    for member in document["members"]:
        view = views.get(member["target"])
        if member.get("started") and view is None:
            raise store.CoordinationError("A participant's public status is unavailable for the task conversation")
        rows.append(
            {
                "target": member["target"],
                "status": (view or {}).get("status", "STARTING"),
                "plan": (view or {}).get("displayed_plan"),
            }
        )
    try:
        return message_context.checked({"kind": "coordinated_task", "participants": rows})
    except ValueError as error:
        raise store.CoordinationError(str(error)) from error


def affected(document, targets):
    """Include dependents of a changed interface, within the authorized member set."""
    allowed = {row["target"] for row in document["members"]}
    if (
        not isinstance(targets, list)
        or not targets
        or any(target not in allowed for target in targets)
        or len(set(targets)) != len(targets)
    ):
        raise store.CoordinationError("Conversation change targets must be unique authorized members")
    selected = set(targets)
    while True:
        expanded = selected | {
            target
            for target, dependencies in document.get("dependencies", {}).items()
            if target in allowed and selected.intersection(dependencies)
        }
        if expanded == selected:
            return [row["target"] for row in document["members"] if row["target"] in selected]
        selected = expanded


def reply(active, source):
    """Reconcile only the exact Requirements response, never historical display text."""
    found = [
        row
        for row in (source.get("chat") or {}).get("messages", [])
        if row.get("role") == "assistant"
        and (row.get("conversation") or {}).get("request_id") == active["source_request_id"]
    ]
    if not found:
        return None
    if len(found) != 1:
        raise store.CoordinationError("Task conversation has multiple matching replies")
    answer = found[0]
    metadata = answer["conversation"]
    if (
        metadata.get("context_sha256") != util.digest(active["context"])
        or metadata.get("intent") not in ("question", "change")
        or not isinstance(answer.get("text"), str)
        or not answer["text"].strip()
    ):
        raise store.CoordinationError("Task conversation reply has stale context or invalid intent")
    try:
        message_context.routing({"context": active["context"]}, metadata)
    except ValueError as error:
        raise store.CoordinationError(str(error)) from error
    return answer


def step(document, requests, views, source):
    """Choose one saved conversation action; this function mutates no authority."""
    if not requests:
        return None
    conversation = document.get("conversation") or {}
    finished = {row["request_id"] for row in conversation.get("results", [])}
    pending = [row for row in requests if row["id"] not in finished]
    active = conversation.get("active")
    current = next((row for row in requests if active and row["id"] == active["id"]), None)
    current = current or (pending[0] if pending else None)
    owned = {
        target: {request_id(document, message, target, kind) for message in requests for kind in ("pause", "feedback")}
        for target in views
    }
    for member in document["members"]:
        if member.get("started") and member["target"] not in views:
            return {"kind": "blocked", "reason": "A conversation participant's public status is unavailable"}
    for target, view in views.items():
        if actions.working(view) or any(
            row.get("active") for row in (view.get("usage") or {}).get("accounting", {}).get("attempts", [])
        ):
            pause = view.get("pause") or {}
            if current and not (active or {}).get("reply") and pause.get("next_command") and not pause.get("requested"):
                return {"kind": "conversation", "operation": "pause", "target": target, "request_id": current["id"]}
            return {"kind": "watch", "target": target}
        intervention = view.get("interventions") or {}
        queued = intervention.get("pending_ids") or []
        if intervention.get("inbox_error") or (intervention.get("pending_count") and not queued):
            return {"kind": "blocked", "reason": "A participant intervention receipt is unavailable"}
        if queued:
            if not set(queued) <= owned[target]:
                return {"kind": "blocked", "reason": "A separate participant message must be reconciled first"}
            return {"kind": "conversation", "operation": "consume", "target": target}
    if not active:
        if pending:
            return {"kind": "conversation", "operation": "begin", "request_id": pending[0]["id"]}
        for target, view in views.items():
            if resumable(view, owned[target]):
                return {"kind": "conversation", "operation": "resume", "target": target}
        return None
    if not current or current["id"] in finished:
        return {"kind": "blocked", "reason": "The active task message has no matching pending receipt"}
    answer = active.get("reply")
    if answer:
        if answer["conversation"]["intent"] == "question":
            return {"kind": "conversation", "operation": "finish"}
        if not active.get("invalidated"):
            return {"kind": "conversation", "operation": "invalidate"}
        for target in active["targets"]:
            if target in active.get("delivered", []):
                continue
            member = next(row for row in document["members"] if row["target"] == target)
            if not member.get("started"):
                return {"kind": "conversation", "operation": "unstarted", "target": target}
            identifier = request_id(document, current, target, "feedback")
            applied = (views[target].get("interventions") or {}).get("applied_receipts", [])
            operation = "delivered" if any(row.get("id") == identifier for row in applied) else "feedback"
            return {"kind": "conversation", "operation": operation, "target": target}
        return {"kind": "conversation", "operation": "finish"}
    if actions.working(source):
        return {"kind": "watch", "target": "Requirements"}
    answer = reply(active, source)
    if answer:
        if context(document, views) != active["context"]:
            return {"kind": "blocked", "reason": "Participant plans changed during the Requirements assessment"}
        return {"kind": "conversation", "operation": "capture"}
    intervention = source.get("interventions") or {}
    need = source.get("needs") or {}
    clarification = source.get("status") == "WAITING_FOR_USER" and need.get("resolver_scope") == "clarification"
    if (
        source.get("status") not in ("RUNNING", "AWAITING_GOAL_APPROVAL")
        and not clarification
        or intervention.get("stop_intent")
        or intervention.get("inbox_error")
        or any(row.get("kind") == "stop" for row in intervention.get("applied_receipts", []))
    ):
        return {"kind": "blocked", "reason": "The source conversation retains an independent operational or user hold"}
    pending_ids = intervention.get("pending_ids") or []
    if (set(pending_ids) - {active["source_request_id"]}) or (intervention.get("pending_count") and not pending_ids):
        return {"kind": "blocked", "reason": "A separate source intervention must be reconciled first"}
    if not (intervention.get("runner_capability") or {}).get("supports_message_context"):
        if clarification and not pending_ids and not active.get("prepared_source"):
            return {"kind": "conversation", "operation": "prepare_source"}
        return {"kind": "blocked", "reason": "The source writer does not yet support task conversation context"}
    received = set(pending_ids) | {row.get("id") for row in intervention.get("applied_receipts", [])}
    return {
        "kind": "conversation",
        "operation": "source_advance" if active["source_request_id"] in received else "submit",
    }
