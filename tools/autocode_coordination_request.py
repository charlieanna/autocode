"""Carry the core's authenticated human request into coordinated task briefs."""


def brief(document):
    """Retain exact source text once, with the opening task for legacy records."""
    texts = [document["task"], *(row["text"] for row in document.get("request_sources", []))]
    return "\n\n".join(dict.fromkeys(text for text in texts if text))


def recovery(document, views, sources):
    """Refresh a legacy unapproved request through ordinary, receipted feedback."""
    if sources is None:
        return None
    expected = brief({**document, "request_sources": sources})
    stale = [row for row in document.get("members", []) if row.get("brief", document["task"]) != expected]
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
    return {"kind": "request_feedback", "target": stale[0]["target"], "message": expected}
