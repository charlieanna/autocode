"""Bounded public task evidence and advisory routing for Requirements replies.

Targets are labels in supplied evidence, never paths, commands or permission.
The coordinating owner alone applies a requested change through normal gates.
"""

import json
from copy import deepcopy

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MAX_BYTES = 131072


def checked(value):
    """Copy a small participant snapshot, rejecting authority-bearing fields."""
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "participants"}
        or value["kind"] != "coordinated_task"
        or not isinstance(value["participants"], list)
        or not 1 <= len(value["participants"]) <= 12
    ):
        raise ValueError("Message context must name one to twelve public task participants")
    targets = set()
    for row in value["participants"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"target", "status", "plan"}
            or not isinstance(row["target"], str)
            or not row["target"].strip()
            or len(row["target"]) > 200
            or row["target"] in targets
            or not isinstance(row["status"], str)
            or len(row["status"]) > 100
            or (row["plan"] is not None and not isinstance(row["plan"], dict))
        ):
            raise ValueError("Message context has an invalid or duplicate participant")
        targets.add(row["target"])
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ValueError("Message context must be finite JSON evidence") from error
    if len(encoded) > MAX_BYTES:
        raise ValueError("Message context exceeds the bounded public evidence size")
    return deepcopy(value)


def schema(base, event):
    """A routed conversation must explicitly name its affected target labels."""
    if not event or not event.get("context"):
        return base
    result = deepcopy(base)
    result["properties"]["affected_targets"] = {
        "type": "array",
        "items": {"type": "string", "enum": [row["target"] for row in event["context"]["participants"]]},
    }
    result["required"].append("affected_targets")
    return result


def routing(event, value):
    """Validate the recommendation before accepting any answer or checkpoint."""
    context = checked(event.get("context"))
    if context is None:
        return {}
    targets = value.get("affected_targets")
    allowed = {row["target"] for row in context["participants"]}
    if (
        not isinstance(targets, list)
        or any(not isinstance(target, str) or target not in allowed for target in targets)
        or len(set(targets)) != len(targets)
        or (value.get("intent") == "change") != bool(targets)
    ):
        raise ValueError("Message context routing must name unique affected participants only for a change")
    return {"affected_targets": list(targets)}


def receipt(event):
    """Public reply metadata for idempotent reconciliation by its task owner."""
    if not event.get("context") or not event.get("reply_after"):
        return {}
    return {
        "conversation": {
            "request_id": event["request_id"],
            "intent": event["intent"],
            "affected_targets": event["affected_targets"],
            "context_sha256": util.digest(event["context"]),
        }
    }
