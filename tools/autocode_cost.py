"""A run's spend against its money cap (#721): one projection for the cap, the view and the cost line.

  spend(state)    known spend of the finished stages, by basis, at the dated price table
  cap(settings)   the saved `settings.limits.max_cost_usd`, or None for no cap
  view(state)     the status view's `cost` field
  spent(state)    the known spend the cap is compared with
  exceeded(state) whether the known spend has reached the cap: the stage-boundary check
  crossing(state, model, record)
                  whether one more attempt on a stronger route, projected from `record`'s tokens,
                  would take the known spend past the cap: the check before a route climb (#611)
  climb_stop(over, ...)
                  the stop reason when that check refuses a climbed attempt
  line(cost)      the one human line `--status` and a completion summary print

Each stage has one basis, as in autocode_usage.stage_rows: the runner's own stages cost nothing,
a provider-reported cost is used as reported, otherwise the stage's tokens are priced from
autocode_prices, otherwise it is unpriced (a model without a rate, or no token counts recorded).
Unpriced is never zero: `spent_usd` is then a floor (`complete` is false), or null when nothing at
all is known. It is also a floor while parallel Builders still run, since their spend reaches the
run only when their batch is accounted. The cap compares the known spend, so an unpriced stage
cannot trip it; `unpriced_stages` and the cost line say so.

Pure over saved state, with no event-file reads, so the build loop can call it at every boundary.
Imports only autocode_usage and autocode_prices, which import only the standard library.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

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


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def spend(state):
    """Known spend: the finished stages' reported and estimated subtotals, and what a checkpoint
    continuation carried from the run it restored (`inherited_spend`), with unpriced and partial counts."""
    rows = usage.stage_rows(state, estimator=prices.estimate)
    reported = [row["cost_usd"] for row in rows if row["basis"] == "reported"]
    estimated = [row["cost_usd"] for row in rows if row["basis"] == "estimated"]
    carried = state.get("inherited_spend") if isinstance(state.get("inherited_spend"), dict) else {}
    inherited = _money(carried.get("usd")) or 0.0
    return {
        "reported_usd": round(sum(reported), 6) if reported else None,
        "estimated_usd": round(sum(estimated), 6) if estimated else None,
        "inherited_usd": round(float(inherited), 6) if inherited else None,
        "known_usd": sum(reported) + sum(estimated) + inherited,
        "unpriced_stages": sum(row["basis"] == "unknown" for row in rows) + _count(carried.get("unpriced_stages")),
        "partial_stages": sum(row["partial"] for row in rows) + _count(carried.get("partial_stages")),
    }


def carried(state):
    """What a checkpoint continuation of this run inherits (`inherited_spend`), or None when it spent nothing."""
    known = spend(state)
    record = {
        "usd": round(float(known["known_usd"]), 6),
        "unpriced_stages": known["unpriced_stages"],
        "partial_stages": known["partial_stages"],
    }
    return record if any(record.values()) else None


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
        "complete": unpriced == 0
        and known["partial_stages"] == 0
        and not state.get("active_stage")
        and not usage.pending_workers(state),
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


def attempt(model, record):
    """What one more attempt on `model` would cost, projected from `record`, a finished stage of the same job:
    its token counts at the model's dated price. {"usd", "input_tokens", "output_tokens"}, or None when the
    model has no rate or the stage recorded no token counts. A stronger route can use more tokens than the
    attempt it follows, so this is an estimate, not a bound."""
    metrics = record.get("metrics") if isinstance(record, dict) else None
    tokens = metrics.get("provider_tokens") if isinstance(metrics, dict) else None
    price = prices.estimate(model, tokens)
    if price is None:
        return None
    return {"usd": price, "input_tokens": tokens["input_tokens"], "output_tokens": tokens["output_tokens"]}


def crossing(state: RunState, model, record):
    """The arithmetic when one more attempt on `model`, projected from `record` (attempt), would take the
    known spend past the cap: {"projected", "spent", "total", "cap", "input_tokens", "output_tokens"}.

    None when there is no cap, no projection, or the attempt fits within the cap. An unknown projection
    never pauses, as an unpriced stage never trips the cap.
    """
    limit = cap(state.get("settings")) if isinstance(state, dict) else None
    projected = attempt(model, record) if limit is not None else None
    if projected is None:
        return None
    used = spent(state)
    total = used + projected["usd"]
    if total <= limit:
        return None
    return {
        "projected": projected["usd"],
        "spent": used,
        "total": total,
        "cap": limit,
        "input_tokens": projected["input_tokens"],
        "output_tokens": projected["output_tokens"],
    }


# The stop reason when crossing() refuses a climbed attempt: the Builder retry policy's stronger attempt
# (autocode_builder_policy) or a role's next effort rung (autocode_climb_cap).
CLIMB_OVER_CAP = (
    "{attempt} ({route}) did not launch: projected at {projected}, {basis}'s {input_tokens:,} input and "
    "{output_tokens:,} output tokens at {model}'s price, it would take the spend from {spent} to {total}, past "
    "the {cap} cost cap. AutoCode never raises the cap; resume with --max-cost-usd {needed} or more (0 for no "
    "cap) to make this attempt"
)


def climb_stop(over, *, attempt, basis, model, effort=None):
    """The stop reason for a climbed attempt that `over` (crossing) refused: `attempt` names it ("The Builder's
    stronger attempt"), `basis` the finished attempt its projection came from ("the last Builder attempt"),
    then the route, the projection, the spend, the total, the cap and the cap to resume with, rounded up to
    the next cent so that a cap of at least that admits the attempt."""
    return CLIMB_OVER_CAP.format(
        attempt=attempt,
        route=f"{model} at {effort} effort" if effort else model,
        basis=basis,
        model=model,
        input_tokens=over["input_tokens"],
        output_tokens=over["output_tokens"],
        **{key: usd(over[key]) for key in ("projected", "spent", "total", "cap")},
        needed=f"{math.ceil(over['total'] * 100) / 100:.2f}",
    )


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
        stages = f"{unpriced} stage{'s' if unpriced != 1 else ''}"
        text += f"; {stages} not priced: a model without a rate or no token counts (autocode models --prices)"
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
