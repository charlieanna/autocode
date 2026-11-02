"""Carry the core's authenticated human request into coordinated task briefs."""


def brief(document, *, target=None):
    """Retain exact source text once, with the opening task for legacy records."""
    texts = [document["task"], *(row["text"] for row in document.get("request_sources", []))]
    member = next((row for row in document.get("members", []) if row["target"] == target), {})
    texts.extend(row["text"] for row in member.get("message_sources", []))
    return "\n\n".join(dict.fromkeys(text for text in texts if text))


def joint_brief(document):
    """Carry applied member messages into joint planning without broadening their scope."""
    messages = {}
    for member in document.get("members", []):
        for source in member.get("message_sources", []):
            key = (source["request_id"], source["text"])
            messages.setdefault(key, []).append(member["target"])
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
            or view.get("approvals")
            or (view.get("usage", {}).get("accounting", {}).get("active_attempts", 0))
            for view in views.values()
        )
    ):
        return {"kind": "blocked", "reason": "Request source recovery requires idle, unapproved member runs"}
    if "request_sources" not in document or any(not row.get("started") for row in stale):
        return {"kind": "sync_request", "sources": sources}
    return {"kind": "request_feedback", "target": stale[0]["target"], "message": expected[stale[0]["target"]]}
