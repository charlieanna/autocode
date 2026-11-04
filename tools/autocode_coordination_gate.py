"""Bind conversational plan replies to complete public coordinated approval packets."""

import re
from copy import deepcopy

try:
    from . import autocode_coordination_message_inbox as inbox
    from . import autocode_coordination_store as store
    from . import autocode_message_context as context
    from . import autocode_util as util
except ImportError:
    import autocode_coordination_message_inbox as inbox
    import autocode_coordination_store as store
    import autocode_message_context as context
    import autocode_util as util


def snapshot(summary):
    """Keep complete approval semantics while excluding commands and polling metadata."""
    need, approval = summary.get("needs") or {}, summary.get("approval") or {}
    if need.get("kind") not in ("approval", "joint_approval") or not approval.get("token"):
        raise ValueError("No complete coordinated approval is currently offered")
    if not re.fullmatch(r"[0-9a-f]{64}", str(approval.get("basis", ""))):
        raise ValueError("The coordinated approval has no exact source and plan identity")
    rows = deepcopy((need.get("summary") or {}).get("runs") or [])
    joint = need.get("joint_plan") or need.get("plan")
    if joint and not any(row.get("role") == "joint" and row.get("plan") == joint for row in rows):
        if any(row.get("target") == "Joint regression" for row in rows):
            raise ValueError("The joint plan differs between approval projections")
        rows.append({"target": "Joint regression", "role": "joint", "plan": deepcopy(joint)})
    plans = [{key: row.get(key) for key in ("target", "role", "plan")} for row in rows]
    members = {row["target"] for row in summary.get("members") or []}
    if {row["target"] for row in plans if row["role"] != "joint"} != members or any(
        row["role"] == "joint" and row["target"] != "Joint regression" for row in plans
    ):
        raise ValueError("The coordinated packet must include every member plan")
    semantic = {
        "basis": approval.get("basis"),
        "id": summary.get("id"),
        "anchor": summary.get("anchor"),
        "kind": need["kind"],
        "source_catalog": summary.get("source_catalog"),
        "members": [
            {
                key: row.get(key)
                for key in (
                    "target",
                    "workspace",
                    "location",
                    "run_dir",
                    "base",
                    "role",
                    "remote",
                    "supporting_evidence",
                )
            }
            for row in summary.get("members") or []
        ],
        "plans": plans,
        "publication": approval.get("publication"),
    }
    return context.projected(
        {
            "kind": "coordinated_plan",
            "participants": [
                {"target": row["target"], "status": "AWAITING_GOAL_APPROVAL", "plan": row["plan"]} for row in plans
            ],
            "approval": {"identity": util.digest(semantic), "publication": approval.get("publication")},
        }
    )


def decision(summary, requests, results):
    """Only the latest interpreted message for this unchanged full packet can confirm."""
    if not requests or not summary.get("approval"):
        return None
    latest = requests[-1]
    found = [row for row in results if row.get("request_id") == latest.get("id")]
    if len(found) != 1:
        return None
    result = found[0]
    if (result.get("reply", {}).get("conversation") or {}).get("intent") != "confirm":
        return None
    current = snapshot(summary)
    if latest.get("plan_context") != current or result.get("context") != current:
        return None
    return {"message_id": latest["id"], "intent": "confirm"}


def submit(run_dir, request_id, text, token, *, read_status):
    """Bind a new reply before queueing; exact retries retain their original evidence."""
    existing = next((row for row in inbox.read(run_dir) if row["id"] == request_id), None)
    if existing:
        if existing.get("plan_token") != token or existing["text"] != text:
            raise store.CoordinationError("Message identity already names different text or displayed plans")
        selected = existing.get("plan_context")
    else:
        shown = read_status(run_dir)
        if not token or (shown.get("approval") or {}).get("token") != token:
            raise store.CoordinationError("The displayed coordinated plans changed; review the current packet")
        selected = snapshot(shown)
    return inbox.submit(run_dir, request_id=request_id, text=text, plan_token=token, plan_context=selected)
