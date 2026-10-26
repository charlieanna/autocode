"""Conserve the original money cap across sequential related task launches."""

try:
    from .autocode_coordination_store import CoordinationError
except ImportError:
    from autocode_coordination_store import CoordinationError


def initial(view):
    cost = view.get("cost") or {}
    return {"cap_usd": cost.get("cap_usd"), "initial_spent_usd": cost.get("spent_usd") or 0.0}


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


def envelope(budget, views, current_run=None):
    """A run keeps its own spent amount; all other spend reduces its cap.

    Match the core's known-cost policy, retaining explicit unknown coverage.
    No pricing estimate is invented for unpriced provider usage.
    """
    cap = budget.get("cap_usd")
    costs = [(row.get("run_dir"), row.get("cost") or {}) for row in views]
    other = budget.get("initial_spent_usd", 0.0) + sum(
        cost.get("spent_usd") or 0 for run, cost in costs if run != current_run
    )
    total = budget.get("initial_spent_usd", 0.0) + sum(cost.get("spent_usd") or 0 for _, cost in costs)
    result = {
        "cap_usd": cap,
        "known_spent_usd": total,
        "complete": all(cost.get("complete") for _, cost in costs),
        "options": [],
    }
    if cap:
        if total >= cap or cap - other <= 0:
            raise CoordinationError("The original coordinated cost cap is exhausted; no further model was launched")
        result["options"] = ["--max-cost-usd", str(cap - other)]
    return result
