"""Carry the core's authenticated human request into coordinated task briefs."""

try:
    from . import autocode_control_replies as control_replies
except ImportError:
    import autocode_control_replies as control_replies


def earlier_context(document, target):
    """Give late arrivals prior clarifications without extending their delivery scope."""
    if target is None:
        return []
    members = document.get("members", [])
    member = next((row for row in members if row["target"] == target), {})
    delivered = {(row["request_id"], row["text"]) for row in member.get("message_sources", [])}
    direct = {document["task"], *(row["text"] for row in document.get("request_sources", []))}
    context = []
    seen = set()
    for result in (document.get("conversation") or {}).get("results", []):
        snapshot = result.get("context") or {}
        participants = {row["target"] for row in snapshot.get("participants", [])}
        if snapshot.get("kind") != "coordinated_task" or not participants or target in participants:
            continue
        if ((result.get("reply") or {}).get("conversation") or {}).get("intent") != "change":
            continue
        identifier, text = result.get("request_id"), result.get("text")
        if not identifier or not isinstance(text, str) or not text.strip():
            continue
        key = (identifier, text)
        if key in seen or key in delivered or text in direct:
            continue
        applied = [
            row["target"]
            for row in members
            if row["target"] in result.get("affected_targets", [])
            and any(
                source.get("request_id") == identifier and source.get("text") == text
                for source in row.get("message_sources", [])
            )
        ]
        if not applied:
            continue
        seen.add(key)
        context.append(
            f"Earlier task clarification (applied to {', '.join(applied)} before {target} joined):\n{text}\n"
            "Reconcile its relevance in this member's requirements and reviewed plan. This is context, "
            "not permission to change another repository or approve a plan."
        )
    return context


def brief(document, *, target=None):
    """Retain exact source text once, with the opening task for legacy records."""
    texts = [
        document["task"],
        *(row["text"] for row in document.get("request_sources", []) if not control_replies.control_record(row)),
    ]
    member = next((row for row in document.get("members", []) if row["target"] == target), {})
    texts.extend(earlier_context(document, target))
    texts.extend(row["text"] for row in member.get("message_sources", []))
    return "\n\n".join(dict.fromkeys(text for text in texts if text))


def joint_brief(document):
    """Carry applied member messages into joint planning without broadening their scope."""
    messages = {}
    for member in document.get("members", []):
        for source in member.get("message_sources", []):
            key = (source["request_id"], source["text"])
            messages.setdefault(key, []).append(member["target"])
    for result in (document.get("conversation") or {}).get("results", []):
        decision = (result.get("reply") or {}).get("conversation") or {}
        if decision.get("intent") == "change" and "Joint regression" in decision.get("affected_targets", []):
            messages.setdefault((result["request_id"], result["text"]), []).append("Joint regression")
    order = {
        row["request_id"]: index for index, row in enumerate((document.get("conversation") or {}).get("results", []))
    }
    parts = [brief(document)]
    for key in sorted(messages, key=lambda key: order.get(key[0], len(order))):
        targets = ", ".join(dict.fromkeys(messages[key]))
        parts.append(f"Applied task message for {targets}:\n{key[1]}")
    return "\n\n".join(parts)


def recovery(document, views, sources):
    """Refresh a legacy unapproved request through ordinary, receipted feedback."""
    if sources is None:
        return None
    expected = {
        row["target"]: brief({**document, "request_sources": sources}, target=row["target"])
        for row in document.get("members", [])
    }
    stale = [
        row for row in document.get("members", []) if row.get("brief", document["task"]) != expected[row["target"]]
    ]
    if "request_sources" in document and not stale:
        return None
    if (
        document.get("approved_contracts")
        or "joint" in document
        or any(
            view.get("approved_contract")
            or any(row.get("kind") != "resume" or row.get("contract_token") for row in view.get("approvals", []))
            or (view.get("usage", {}).get("accounting", {}).get("active_attempts", 0))
            for view in views.values()
        )
    ):
        return {"kind": "blocked", "reason": "Request source recovery requires idle, unapproved member runs"}
    if "request_sources" not in document or any(not row.get("started") for row in stale):
        return {"kind": "sync_request", "sources": sources}
    return {"kind": "request_feedback", "target": stale[0]["target"], "message": expected[stale[0]["target"]]}
