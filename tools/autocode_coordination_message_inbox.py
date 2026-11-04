"""Pending-only task messages, independent of the coordinator's advance lock.

Only the coordinator owns this inbox. It contains original human text and stable
receipts, never task checkpoints, model output or authority to resume or approve.
The scheduler will retain its separate processing results under the advance lock;
accepted requests remain here so a lost response can always be reconciled.
"""

from __future__ import annotations

import contextlib
import json

try:
    from . import autocode_coordination_store as store
    from . import autocode_message_context as context
    from . import autocode_util as util
except ImportError:
    import autocode_coordination_store as store
    import autocode_message_context as context
    import autocode_util as util


def validate(request_id, text):
    """Bound identifiers and human text without changing the submitted text."""
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise store.CoordinationError("Message request identity must contain 1 to 128 characters")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65536:
        raise store.CoordinationError("Message text must be nonempty and at most 64 KiB")


def paths(run_dir):
    """Reject redirected coordinator storage before any read or lock creation."""
    directory = store.root(run_dir)
    inbox, lock = directory / "messages.json", directory / "messages.lock"
    if any(path.is_symlink() for path in (directory, inbox, lock)):
        raise store.CoordinationError("Coordination message inbox and lock cannot be a symlink")
    return inbox, lock


def read(run_dir):
    """Return immutable submissions in order; reading never creates storage."""
    inbox, _ = paths(run_dir)
    if not inbox.exists():
        return []
    document = store.read(run_dir)
    try:
        value = json.loads(inbox.read_text())
    except (OSError, ValueError) as error:
        raise store.CoordinationError("Coordination message inbox is unreadable; retained for inspection") from error
    if (
        not document
        or not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("anchor") != document["anchor"]
        or value.get("coordination_id") != document.get("id")
        or not isinstance(value.get("requests"), list)
    ):
        raise store.CoordinationError("Coordination message inbox has an invalid shape or task identity")
    seen = set()
    for order, row in enumerate(value["requests"], 1):
        if not isinstance(row, dict):
            raise store.CoordinationError("Coordination message inbox contains an invalid request")
        validate(row.get("id"), row.get("text"))
        if (
            row["id"] in seen
            or row.get("kind") != "message"
            or type(row.get("order")) is not int
            or row["order"] != order
            or row.get("coordination_id") != document["id"]
            or not isinstance(row.get("submitted_at"), str)
            or not row["submitted_at"]
        ):
            raise store.CoordinationError("Coordination message inbox contains an invalid or repeated request")
        if row.get("plan_token") is not None or row.get("plan_context") is not None:
            try:
                if (
                    not isinstance(row.get("plan_token"), str)
                    or not row["plan_token"]
                    or (row.get("plan_context") or {}).get("kind") != "coordinated_plan"
                ):
                    raise ValueError("Missing approval binding")
                context.checked(row["plan_context"])
            except (ValueError, TypeError, AttributeError) as error:
                raise store.CoordinationError("Coordination message has an invalid approval snapshot") from error
        seen.add(row["id"])
    return value["requests"]


@contextlib.contextmanager
def locked(run_dir):
    """Keep submission bounded when another writer holds the short inbox lock."""
    _, lock = paths(run_dir)
    with lock.open("a+") as handle:
        try:
            util.lock_file(handle)
        except BlockingIOError as error:
            raise store.CoordinationError("Coordination message inbox is busy; retry the same request") from error
        try:
            yield
        finally:
            util.unlock_file(handle)


def submit(run_dir, *, request_id, text, plan_token=None, plan_context=None):
    """Save once or reconcile an exact retry, without dispatching any task action."""
    validate(request_id, text)
    if plan_token is not None or plan_context is not None:
        try:
            if (
                not isinstance(plan_token, str)
                or not plan_token
                or (plan_context or {}).get("kind") != "coordinated_plan"
            ):
                raise ValueError("Missing approval binding")
            plan_context = context.checked(plan_context)
        except (ValueError, TypeError, AttributeError) as error:
            raise store.CoordinationError("Plan reply requires an exact displayed approval snapshot") from error
    if not store.read(run_dir):
        raise store.CoordinationError("Coordination has not started")
    with locked(run_dir):
        document = store.read(run_dir)
        if not document or not isinstance(document.get("id"), str) or not document["id"]:
            raise store.CoordinationError("Coordination identity is unavailable")
        rows = read(run_dir)
        existing = next((row for row in rows if row["id"] == request_id), None)
        if existing:
            if existing["text"] != text:
                raise store.CoordinationError("Message request identity is already used by different text")
            if existing.get("plan_token") != plan_token or existing.get("plan_context") != plan_context:
                raise store.CoordinationError("Message request identity is already bound to different displayed plans")
            receipt = existing
        else:
            if document.get("phase") in ("publishing", "complete"):
                raise store.CoordinationError("New messages are unavailable after publication has begun")
            receipt = {
                "id": request_id,
                "kind": "message",
                "text": text,
                "order": len(rows) + 1,
                "submitted_at": util.now(),
                "coordination_id": document["id"],
                **({"plan_token": plan_token, "plan_context": plan_context} if plan_context is not None else {}),
            }
            inbox, _ = paths(run_dir)
            util.atomic_json(
                inbox,
                {
                    "version": 1,
                    "anchor": document["anchor"],
                    "coordination_id": document["id"],
                    "requests": [*rows, receipt],
                },
            )
    return {
        "version": 1,
        "accepted": True,
        "idempotent": existing is not None,
        "consumer": "pending_only",
        "receipt": receipt,
    }
