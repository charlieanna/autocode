"""Explicit, checkpoint-bound recovery of a coordination's shared money cap.

Only the coordinator's own ledger is changed. A stopped member is resumed through
TaskRun by the caller, with its public recovery token and the derived envelope.
An interrupted call keeps durable intent and is never dispatched a second time.
"""

from __future__ import annotations

import math
from pathlib import Path

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_budget as budget
    from . import autocode_coordination_store as store
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_budget as budget
    import autocode_coordination_store as store
    import autocode_util as util


def cost_pause(view):
    return (view.get("recovery") or {}).get("cause") == "PAUSED_COST_LIMIT" or view.get("status") == "PAUSED_COST_LIMIT"


def _settled(view):
    need = view.get("needs") or {}
    return (
        view.get("done")
        or cost_pause(view)
        or need.get("kind") in ("approve_plan", "continue", "review")
        or (need.get("kind") == "answer" and not need.get("resolver_scope"))
    )


def _bound(view):
    try:
        workspace, run_dir = Path(view["workspace"]), Path(view["run_dir"])
        offered = actions.bound_offer(view, workspace, run_dir)
        if offered is not None:
            row, _ = offered
        else:
            command = actions.parse(view["next_command"], *actions.own(view, workspace, run_dir))
            rows = actions.integer_inputs(view, command)
            if len(rows) != 1:
                return None
            row = rows[0]
        return row if row["flag"] == "--max-cost-usd" else None
    except (KeyError, TypeError, ValueError, actions.ClientGap):
        return None


def offer(document, rows, history):
    """A finite shared-cap decision, only at a settled cost boundary."""
    cap = (document.get("budget") or {}).get("cap_usd")
    if (
        type(cap) not in (int, float)
        or not math.isfinite(cap)
        or cap <= 0
        or document.get("pending")
        or document.get("rework")
        or document.get("phase") in ("complete", "publishing", "cancelled")
        or store.cancelled(document["anchor"])
        or any(actions.working(row["view"]) for row in rows)
        or any(not _settled(row["view"]) for row in rows)
    ):
        return None
    summary = budget.accounting(document["budget"], history)
    stopped = next((row for row in rows if cost_pause(row["view"])), None)
    if stopped:
        bound = _bound(stopped["view"])
        if bound is None:
            return None
        spent = (stopped["view"].get("cost") or {}).get("spent_usd") or 0.0
        minimum = summary["known_spent_usd"] - spent + bound["minimum"]
    elif summary["known_spent_usd"] >= cap:
        minimum = summary["known_spent_usd"] + 0.01
    else:
        return None
    minimum = max(round(minimum, 6), float(cap))
    identity = {
        "coordination": document["id"],
        "revision": document.get("revision", 0),
        "accounting": summary,
        "participants": [
            {
                "run_dir": row["run_dir"],
                **{key: row["view"].get(key) for key in ("status", "turn", "iteration", "next_stage", "cost")},
            }
            for row in rows
        ],
        "stopped": stopped
        and {
            "run_dir": stopped["run_dir"],
            "recovery_token": (stopped["view"].get("recovery") or {}).get("token"),
        },
    }
    token = util.digest(identity)
    return {
        "kind": "budget",
        **summary,
        "minimum_cap_usd": minimum,
        "target": stopped["target"] if stopped else None,
        "token": token,
        "next_command": store.command(document["anchor"], "budget", "--token", token, "--max-cost-usd", "N", "--json"),
        "effect": "Set a finite whole-set accounting cap, retain all prior spend and other limits, and acknowledge only the displayed cost pause. This approves no plan and waives no check.",
    }


def amount(value, minimum):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise store.CoordinationError("Enter a finite shared cost cap in USD") from None
    if isinstance(value, bool) or not math.isfinite(number) or not minimum <= number <= actions.MAX_SAFE_INTEGER:
        raise store.CoordinationError(f"The shared cost cap must be finite and at least {minimum:g} USD")
    return number


def apply(document, offered, token, value, rows, *, resume):
    if store.cancelled(document["anchor"]) or not offered or offered["token"] != token:
        raise store.CoordinationError("The shared-budget checkpoint changed; read coordination status again")
    selected = amount(value, offered["minimum_cap_usd"])
    old = document["budget"]["cap_usd"]
    document["budget"].setdefault("revisions", []).append(
        {
            "previous_cap_usd": old,
            "cap_usd": selected,
            "known_spent_usd": offered["known_spent_usd"],
            "accounting_complete": offered["complete"],
            "at": util.now(),
        }
    )
    document["budget"]["cap_usd"] = selected
    stopped = next((row for row in rows if row["target"] == offered["target"]), None)
    if stopped:
        document["pending"] = {"operation": "budget_resume", "target": stopped["target"]}
    store.save(document)
    if stopped:
        resume(stopped, (stopped["view"].get("recovery") or {})["token"])
        document["pending"] = None
        store.save(document)
