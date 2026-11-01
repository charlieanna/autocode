"""Explicit recovery of a settled cancellation, using public participant views."""

from __future__ import annotations

import json

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_budget as budget
    from . import autocode_coordination_limits as limits
    from . import autocode_coordination_planning_limits as planning
    from . import autocode_coordination_repositories as repositories
    from . import autocode_coordination_store as store
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_budget as budget
    import autocode_coordination_limits as limits
    import autocode_coordination_planning_limits as planning
    import autocode_coordination_repositories as repositories
    import autocode_coordination_store as store
    import autocode_util as util


def offer(document, rows, history):
    """A cancellation may be acknowledged only after all owned work settles."""
    if not store.cancelled(document["anchor"]) or document.get("pending") or document.get("rework"):
        return None
    if document.get("phase") in ("complete", "publishing"):
        return None
    present = {row["run_dir"] for row in rows}
    if any(row.get("started") and row["run_dir"] not in present for row in document["members"]):
        return None
    for row in rows:
        view = row["view"]
        need = view.get("needs") or {}
        if need.get("kind") == "continue" and need.get("action"):
            return None
        attempts = (view.get("usage") or {}).get("accounting", {}).get("attempts", [])
        intervention = (
            view.get("status") == "PAUSED_INTERVENTION"
            and (view.get("needs") or {}).get("kind") == "resume"
            and (view.get("recovery") or {}).get("cause") == "PAUSED_INTERVENTION"
        )
        if actions.working(view) or any(a.get("active") for a in attempts):
            return None
        if not intervention and not planning.settled(view):
            return None
    summary = budget.accounting(document.get("budget") or {}, history)
    if not summary["complete"]:
        return None
    if document.get("source_catalog") and repositories.source_snapshot() != document["source_catalog"]:
        return None
    request = json.loads((store.root(document["anchor"]) / "cancel-request.json").read_text())
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
                "needs",
                "recovery",
                "cost",
                "scope",
                "routes",
                "related_profile",
                "request_sources",
                "approved_contract",
                "displayed_plan",
            )
        }
        | {
            "accounting": (view.get("usage") or {}).get("accounting"),
            "checkpoint": (view.get("observation") or {}).get("checkpoint_token"),
        }
        for view in history
    ]
    sources = {row["run_dir"]: repositories.fingerprint(row["workspace"]) for row in rows}
    token = util.digest({"coordination": document, "cancel": request, "participants": frontier, "sources": sources})
    cap = summary["cap_usd"] or 0
    return {
        "kind": "coordination_resume",
        **summary,
        "current_cap_usd": cap,
        "minimum_cap_usd": max(cap, summary["known_spent_usd"] + 0.000001),
        "allows_no_cap": True,
        "token": token,
        "cancel_request": request,
        "next_command": store.command(document["anchor"], "resume", "--token", token, "--max-cost-usd", "N", "--json"),
        "effect": "Resume saved coordination without replaying completed work. An explicit 0 removes the shared "
        "spending cap and planning allocation limit. Prior spend, attempts, routes and other limits remain. "
        "This action launches no model, approves no plan and waives no verification gate.",
    }


def apply(document, offered, token, value):
    """Acknowledge exactly one cancellation; a newer concurrent cancellation wins."""
    if not offered or offered.get("kind") != "coordination_resume" or offered.get("token") != token:
        raise store.CoordinationError("The cancelled-coordination checkpoint changed; read status again")
    selected = 0 if type(value) in (int, float) and value == 0 else limits.amount(value, offered["minimum_cap_usd"])
    request = json.loads((store.root(document["anchor"]) / "cancel-request.json").read_text())
    if request != offered["cancel_request"]:
        raise store.CoordinationError("A new cancellation arrived; no cancellation was acknowledged")
    saved = document["budget"]
    saved.setdefault("revisions", []).append(
        {
            "previous_cap_usd": saved.get("cap_usd"),
            "cap_usd": selected,
            "known_spent_usd": offered["known_spent_usd"],
            "accounting_complete": offered["complete"],
            "token": token,
            "at": util.now(),
            "reason": "explicit_cancel_recovery",
        }
    )
    saved["cap_usd"] = selected
    document.setdefault("resumptions", []).append({"cancel_request": request, "token": token, "at": util.now()})
    store.save(document)
