"""Explicit planning reallocation inside an unchanged coordinated task cap."""

from __future__ import annotations

import math

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_budget as budget
    from . import autocode_coordination_limits as limits
    from . import autocode_coordination_repositories as repositories
    from . import autocode_coordination_store as store
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_budget as budget
    import autocode_coordination_limits as limits
    import autocode_coordination_repositories as repositories
    import autocode_coordination_store as store
    import autocode_util as util


def settled(view):
    """Permit only settled work, including the exact coordinator-owned scope question."""
    if actions.working(view) or any(
        attempt.get("active") for attempt in (view.get("usage") or {}).get("accounting", {}).get("attempts", [])
    ):
        return False
    need = view.get("needs") or {}
    if view.get("done") or limits.cost_pause(view) or need.get("kind") in ("continue", "approve_plan", "review"):
        return True
    questions = need.get("questions") or []
    return (
        need.get("kind") == "answer"
        and need.get("resolver_scope") == "clarification"
        and len(questions) == 1
        and questions[0].get("id") == "workspace-scope-" + str((view.get("scope") or {}).get("identity"))
    )


def offer(document, rows, history):
    """Offer an explicit total allocation; neither a restart nor a cap acknowledgement renews it."""
    saved = document.get("budget") or {}
    allowance = saved.get("planning_allowance") or {}
    cap, planning_cap = saved.get("cap_usd"), allowance.get("cap_usd")
    if (
        any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in (cap, planning_cap))
        or document.get("pending")
        or document.get("rework")
        or document.get("phase") in ("complete", "publishing", "cancelled")
        or store.cancelled(document["anchor"])
        or any(not settled(row["view"]) for row in rows)
        or any(
            row.get("started") and row["run_dir"] not in {item["run_dir"] for item in rows}
            for row in document["members"]
        )
    ):
        return None
    costs = [allowance.get("initial") or {}, *(budget.planning_spend(view) for view in history)]
    summary = budget.accounting(saved, history)
    if not summary["complete"] or not all(cost.get("complete") is True for cost in costs):
        return None
    spent = sum(cost.get("spent_usd") or 0 for cost in costs)
    minimum = max(planning_cap, spent) + 0.000001
    projected_stop = False
    for row in rows:
        if limits.cost_pause(row["view"]):
            bound = limits._bound(row["view"])
            if bound is None:
                return None
            required = spent + bound["minimum"] - (row["view"].get("cost") or {}).get("spent_usd", 0)
            minimum = max(minimum, required)
            projected_stop = projected_stop or required > planning_cap
    maximum = cap - max(0, summary["known_spent_usd"] - spent)
    if (spent < planning_cap and not projected_stop) or minimum > maximum:
        return None
    # Omit polling clocks and controller display text; retain the entire durable
    # coordination and all relevant public participant/source/attempt frontiers.
    frontier = [
        {
            key: view.get(key)
            for key in (
                "run_dir",
                "status",
                "phase",
                "turn",
                "iteration",
                "next_stage",
                "current_task",
                "scope",
                "request_sources",
                "related_profile",
                "approved_contract",
                "displayed_plan",
                "needs",
                "recovery",
                "cost",
            )
        }
        | {
            "accounting": (view.get("usage") or {}).get("accounting"),
            "checkpoint": (view.get("observation") or {}).get("checkpoint_token"),
        }
        for view in history
    ]
    sources = {row["run_dir"]: repositories.fingerprint(row["workspace"]) for row in rows}
    token = util.digest({"coordination": document, "participants": frontier, "sources": sources})
    return {
        "kind": "planning_budget",
        **summary,
        "planning_cap_usd": planning_cap,
        "planning_spent_usd": spent,
        "minimum_cap_usd": round(minimum, 6),
        "maximum_cap_usd": maximum,
        "token": token,
        "next_command": store.command(
            document["anchor"], "planning-budget", "--token", token, "--max-planning-cost-usd", "N", "--json"
        ),
        "effect": "Reallocate unused task money to this total planning allowance. The whole-set cap, prior spend, "
        "work and other limits stay unchanged. This launches no model, approves no plan and waives no check.",
    }


def apply(document, offered, token, value):
    """Record one exact allocation revision without touching any task or dispatching a model."""
    if not offered or offered.get("kind") != "planning_budget" or offered.get("token") != token:
        raise store.CoordinationError("The planning-budget checkpoint changed; read coordination status again")
    selected = limits.amount(value, offered["minimum_cap_usd"])
    if selected > offered["maximum_cap_usd"]:
        raise store.CoordinationError("The planning allowance cannot exceed the remaining whole-set allocation")
    allowance = document["budget"]["planning_allowance"]
    allowance.setdefault("revisions", []).append(
        {
            "previous_cap_usd": allowance["cap_usd"],
            "cap_usd": selected,
            "known_spent_usd": offered["planning_spent_usd"],
            "whole_set_cap_usd": document["budget"]["cap_usd"],
            "token": token,
            "at": util.now(),
        }
    )
    allowance["cap_usd"] = selected
    store.save(document)
