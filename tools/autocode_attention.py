"""Stable attention for the current saved human boundary or completed turn.

Pure projection. An attention identity is never an approval/answer token. The
existing chat context binds decisions; existing follow-up receipts bind outcomes.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def project(state: RunState, public, *, workspace, run_dir, context):
    result: dict[str, Any] = {
        "version": 1,
        "item": None,
        "acknowledgement": {"available": False, "state": "unknown"},
        "acknowledge": None,
    }
    if workspace is None or run_dir is None:
        return result
    incarnation = {
        key: state.get(key) for key in ("created_at", "task_id") if isinstance(state.get(key), str) and state.get(key)
    }
    if not incarnation:
        result["reason"] = "This legacy checkpoint lacks a stable saved run identity"
        return result
    need = public.get("needs") or {}
    kind = need.get("kind")
    if public.get("done"):
        turns = state.get("turns") or []
        if not isinstance(turns, list) or (turns and not isinstance(turns[-1], dict)):
            return result
        anchor = turns[-1].get("event_id") if turns else "initial"
        # Legacy turns already have a native position. No new counter is saved.
        anchor = anchor or {"legacy_turn": len(turns)}
        category, label = "completion", "Run finished; inspect evidence"
    else:
        if kind in (None, "continue", "dependency") or not context:
            return result
        anchor = context
        if kind == "approve_plan":
            category, label = "plan_approval", "Plan approval needed"
        elif kind == "answer" and not need.get("route") and need.get("resolver_scope") != "operational_exhaustion":
            category, label = "clarification", "Clarification needed"
        else:
            category, label = "decision", "Decision needed"
    location = {"workspace": str(workspace), "run_dir": str(run_dir)}
    result["item"] = {
        "id": util.digest({"version": 1, **location, "incarnation": incarnation, "kind": category, "anchor": anchor}),
        **location,
        "kind": category,
        "label": label,
        "summary": util.redact(
            next((line.strip() for line in str(state.get("task") or "").splitlines() if line.strip()), "")
        )[:80],
    }
    return result


def with_acknowledgement(value, receipt):
    result = deepcopy(value)
    result["acknowledgement"] = deepcopy(receipt)
    item = result.get("item")
    if item and receipt.get("available") is True:
        result["acknowledge"] = {
            "label": "Acknowledge alert; leave task unchanged",
            "next_command": [
                "autocode",
                "attention",
                "ack",
                "--workspace",
                item["workspace"],
                "--run-dir",
                item["run_dir"],
                "--item-id",
                item["id"],
            ],
        }
    return result
