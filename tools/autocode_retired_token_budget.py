"""Compatibility for saved runs created before cumulative token budgets were removed.

Usage ledgers remain evidence; only the retired setting and its operational pause
are recognized here. No runtime module consumes this setting as a limit.
"""

LEGACY_LIMIT = "max_reported_tokens"


def retire_settings(settings):
    """Remove the retired policy from a caller-owned settings copy."""
    for key in ("limits", "budget_origins"):
        container = settings.get(key)
        if isinstance(container, dict):
            container.pop(LEGACY_LIMIT, None)


def retired_pause(origin):
    """Identify only the old token guard, never an approval or another budget."""
    if not isinstance(origin, dict):
        return False
    budget = origin.get("budget")
    return (origin.get("pause_status") in ("PAUSED_BUDGET", "PAUSED_USAGE_UNKNOWN")
            and isinstance(budget, dict) and budget.get("kind") == LEGACY_LIMIT)
