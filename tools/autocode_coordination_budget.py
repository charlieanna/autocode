"""Conserve the original money cap across sequential related task launches."""

try:
    from . import autocode_roles as roles
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_roles as roles
    from autocode_coordination_store import CoordinationError


def initial(view):
    cost = view.get("cost") or {}
    result = {
        "cap_usd": cost.get("cap_usd"),
        "initial_spent_usd": cost.get("spent_usd") or 0.0,
        "initial_complete": cost.get("complete") is True,
    }
    if cost.get("cap_usd"):
        result["planning_allowance"] = {
            "cap_usd": float(cost["cap_usd"]) * 0.3,
            "initial": planning_spend(view),
        }
    return result


def planning_spend(view):
    """Planning's public ledger, including rejected reports and previous cycles.

    Missing legacy attribution is charged conservatively to planning; missing
    prices remain incomplete. Groupings are alternatives, never added together.
    """
    cost = view.get("cost") or {}
    groups = (cost.get("breakdown") or {}).get("by_stage")
    if groups is None:
        return {"spent_usd": cost.get("spent_usd") or 0, "complete": cost.get("complete") is True}
    rows = [row for row in groups if row.get("stage", "").removesuffix("_report_repair") in roles.ALL_PLANNING_STAGES]
    return {
        "spent_usd": sum(row.get("spent_usd") or 0 for row in rows),
        "complete": all(row.get("complete") is True for row in rows),
    }


def planning_ceiling(budget, views, current_run):
    """The current run's whole-run cap while planning, or None for approved work.

    The allocation is fixed when coordination begins. Reasserting the total cap,
    replanning, a new joint run or restarting a viewer never replenishes it.
    Existing saved coordinations retain their original policy.
    """
    allowance = budget.get("planning_allowance")
    if not allowance or budget.get("cap_usd") == 0:
        return None
    current = next((row for row in views if row.get("run_dir") == current_run), {}) if current_run else {}
    stage = (current.get("next_stage") or "").removesuffix("_report_repair")
    if current.get("approved_contract") and stage not in roles.ALL_PLANNING_STAGES:
        return None
    costs = [allowance["initial"], *(planning_spend(row) for row in views)]
    if not all(row["complete"] for row in costs):
        raise CoordinationError(
            "Coordinated planning cost is unpriced or incomplete; reconcile accounting before more planning"
        )
    remaining = allowance["cap_usd"] - sum(row["spent_usd"] for row in costs)
    if remaining <= 0:
        raise CoordinationError(
            "The shared planning allowance is exhausted; implementation and verification money was reserved. No further planning was launched"
        )
    return ((current.get("cost") or {}).get("spent_usd") or 0) + remaining


def verification_view(verification):
    """Account for independent reviewers too, including a failed joint attempt."""
    document = (verification.get("evidence_report") or {}).get("document") or {}
    attempts = document.get("attempts") or []
    costs = [
        row.get("reported_cost_usd")
        if row.get("reported_cost_usd") is not None
        else row.get("historical_estimated_cost_usd")
        for row in attempts
    ]
    known = [value for value in costs if value is not None]
    return {
        "run_dir": "verification:" + str(verification.get("record")),
        "cost": {
            "spent_usd": sum(known) if known else None,
            "complete": bool(attempts) and all(value is not None for value in costs),
        },
    }


def accounting(budget, views):
    """Report retained known spend even when the envelope is exhausted."""
    costs = [row.get("cost") or {} for row in views]
    return {
        "cap_usd": budget.get("cap_usd"),
        "known_spent_usd": budget.get("initial_spent_usd", 0.0) + sum(cost.get("spent_usd") or 0 for cost in costs),
        "complete": budget.get("initial_complete") is True and all(cost.get("complete") for cost in costs),
    }


def envelope(budget, views, current_run=None):
    """A run keeps its own spent amount; all other spend reduces its cap.

    Match the core's known-cost policy, retaining explicit unknown coverage.
    No pricing estimate is invented for unpriced provider usage.
    """
    views = list(views)
    cap = budget.get("cap_usd")
    costs = [(row.get("run_dir"), row.get("cost") or {}) for row in views]
    other = budget.get("initial_spent_usd", 0.0) + sum(
        cost.get("spent_usd") or 0 for run, cost in costs if run != current_run
    )
    result = {**accounting(budget, views), "options": []}
    if cap == 0:
        result["options"] = ["--max-cost-usd", "0"]
    total = result["known_spent_usd"]
    if cap:
        if total >= cap or cap - other <= 0:
            raise CoordinationError("The original coordinated cost cap is exhausted; no further model was launched")
        ceiling = planning_ceiling(budget, views, current_run)
        result["options"] = ["--max-cost-usd", str(min(cap - other, ceiling) if ceiling is not None else cap - other)]
    return result
