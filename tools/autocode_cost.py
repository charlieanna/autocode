"""A run's spend against its money cap (#721): one projection for the cap, the view and the cost line.

  spend(state)    known spend of the finished stages, by basis, at the dated price table
  cap(settings)   the saved `settings.limits.max_cost_usd`, or None for no cap
  view(state)     the status view's `cost` field
  spent(state)    the known spend the cap is compared with
  exceeded(state) whether the known spend has reached the cap: the stage-boundary check
  line(cost)      the one human line `--status`, a completion and a cost pause print

Each stage has one basis, as in autocode_usage.stage_rows: the runner's own stages cost nothing,
a provider-reported cost is used as reported, otherwise the stage's tokens are priced from
autocode_prices, otherwise it is unpriced. Unpriced is never zero: `spent_usd` is then a floor
(`complete` is false), or null when nothing at all is known. The cap compares the known spend, so
a stage without a rate cannot trip it; `unpriced_stages` and the cost line say so.

Pure over saved state, with no event-file reads, so the build loop can call it at every boundary.
Imports only autocode_usage and autocode_prices, which import only the standard library.
"""

from __future__ import annotations

import math

try:
    from . import autocode_prices as prices
    from . import autocode_usage as usage
except ImportError:
    import autocode_prices as prices
    import autocode_usage as usage

FIELDS = (
    "estimated_usd",
    "reported_usd",
    "inherited_usd",
    "spent_usd",
    "cap_usd",
    "unpriced_stages",
    "complete",
    "prices_as_of",
)


def _money(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def cap(settings):
    """The saved cap in USD, or None when there is none (absent, 0, or not a usable number)."""
    limits = settings.get("limits") if isinstance(settings, dict) else None
    value = _money(limits.get("max_cost_usd")) if isinstance(limits, dict) else None
    return float(value) if value else None


def spend(state):
    """Known spend: the finished stages' reported and estimated subtotals, the spend a checkpoint
    continuation carried from the run it restored (`inherited_spend_usd`), unpriced and partial counts."""
    rows = usage.stage_rows(state, estimator=prices.estimate)
    reported = [row["cost_usd"] for row in rows if row["basis"] == "reported"]
    estimated = [row["cost_usd"] for row in rows if row["basis"] == "estimated"]
    inherited = _money(state.get("inherited_spend_usd")) or 0.0
    return {
        "reported_usd": round(sum(reported), 6) if reported else None,
        "estimated_usd": round(sum(estimated), 6) if estimated else None,
        "inherited_usd": round(float(inherited), 6) if inherited else None,
        "known_usd": sum(reported) + sum(estimated) + inherited,
        "unpriced_stages": sum(row["basis"] == "unknown" for row in rows),
        "partial_stages": sum(row["partial"] for row in rows),
    }


def view(state):
    """The status view's `cost` field. Never raises on a damaged state."""
    state = state if isinstance(state, dict) else {}
    known = spend(state)
    unpriced = known["unpriced_stages"]
    nothing_known = all(known[key] is None for key in ("reported_usd", "estimated_usd", "inherited_usd"))
    try:
        as_of = prices.table()["as_of"]
    except (OSError, ValueError):
        as_of = None
    return {
        "estimated_usd": known["estimated_usd"],
        "reported_usd": known["reported_usd"],
        "inherited_usd": known["inherited_usd"],
        "spent_usd": None if unpriced and nothing_known else round(float(known["known_usd"]), 6),
        "cap_usd": cap(state.get("settings")),
        "unpriced_stages": unpriced,
        "complete": unpriced == 0 and known["partial_stages"] == 0 and not state.get("active_stage"),
        "prices_as_of": as_of,
    }


def spent(state):
    """The known spend in USD that the cap is compared with (reported plus estimated; unpriced excluded)."""
    return spend(state)["known_usd"]


def exceeded(state):
    """(spent, cap) when the run's known spend has reached its cap, else None."""
    limit = cap(state.get("settings"))
    if limit is None:
        return None
    used = spent(state)
    return (used, limit) if used >= limit else None


def usd(value):
    """$1.23; below a cent, up to six places, so a small total or cap is never shown as $0.00."""
    if value == 0 or value >= 0.01:
        return f"${value:,.2f}"
    places = f"{value:.6f}".rstrip("0")
    return "under $0.000001" if places.endswith(".") else "$" + places


def line(cost):
    """One line for a person, from a `cost` view field."""
    cost = cost if isinstance(cost, dict) else {}
    spent, unpriced = cost.get("spent_usd"), cost.get("unpriced_stages") or 0
    parts = [
        f"{usd(cost[key])} {label}"
        for key, label in (
            ("estimated_usd", "estimated"),
            ("reported_usd", "reported by the provider"),
            ("inherited_usd", "before the restored checkpoint"),
        )
        if _money(cost.get(key)) is not None
    ]
    if spent is None:
        text = "Cost: unknown"
    else:
        floor = "at least " if not cost.get("complete") else ""
        text = f"Cost: {floor}{usd(spent)} so far" + (f" ({', '.join(parts)})" if len(parts) > 1 else "")
    if unpriced:
        text += f"; {unpriced} stage{'s' if unpriced != 1 else ''} without a rate (autocode models --prices)"
    limit = _money(cost.get("cap_usd"))
    return text + (f"; cap {usd(limit)}." if limit else "; no cap.")


def check(sample):
    """A `cost` block copied from a status view (the reliability table's per-case rows), or ValueError."""
    if not isinstance(sample, dict):
        raise ValueError("a cost sample must be an object")
    for key in ("estimated_usd", "reported_usd", "spent_usd", "cap_usd"):
        value = sample.get(key)
        if value is not None and _money(value) is None:
            raise ValueError(f"{key} must be null or a finite nonnegative number")
    return {key: sample.get(key) for key in ("estimated_usd", "reported_usd", "spent_usd", "cap_usd")}
