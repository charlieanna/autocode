"""Display existing public accounting and route projections; never calculate spend or choose a route."""

from __future__ import annotations

import math


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _count(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _label(value, default="unknown") -> str:
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else default


def _money(value) -> str:
    if not _number(value):
        return "unknown"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    whole, _, fraction = text.partition(".")
    return f"${whole}.{fraction.ljust(2, '0')}"


def _quantity(value, *, money=False) -> str:
    if isinstance(value, dict):
        complete = value.get("complete") is True
        amount = value.get("value") if complete else value.get("known")
        suffix = "" if complete else " known (incomplete)"
    else:
        amount, suffix = value, ""
    if not _number(amount):
        return "unknown"
    return (_money(amount) if money else f"{amount:,.0f}") + suffix


def _accounting(view: dict) -> dict:
    return _mapping(_mapping(view.get("usage")).get("accounting"))


def _gaps(view: dict) -> list[str]:
    cost, accounting = _mapping(view.get("cost")), _accounting(view)
    gaps = []
    if unpriced := _count(cost.get("unpriced_stages")):
        gaps.append(f"{unpriced} unpriced")
    if active := _count(accounting.get("active_attempts")):
        gaps.append(f"{active} active")
    pending = accounting.get("parallel_workers_pending")
    if isinstance(pending, list) and pending:
        gaps.append(f"{len(pending)} worker{'s' if len(pending) != 1 else ''} pending")
    if accounting.get("complete") is False:
        gaps.append("attempt coverage incomplete")
    elif accounting.get("complete") is not True:
        gaps.append("attempt coverage unavailable")
    return gaps


def _complete(view: dict) -> bool:
    cost = _mapping(view.get("cost"))
    return cost.get("complete") is True and _number(cost.get("spent_usd")) and not _gaps(view)


def available(view: dict) -> bool:
    return any(isinstance(view.get(key), dict) and view[key] for key in ("cost", "usage", "routes"))


def compact(view: dict) -> tuple[str, list[str]] | None:
    """A short sidebar summary; full accounting and routes remain available through /details."""
    if not available(view):
        return None
    cost = _mapping(view.get("cost"))
    complete = _complete(view)
    summary = f"{_money(cost.get('spent_usd'))} accounted"
    rows = ["complete accounting" if complete else "incomplete · " + (", ".join(_gaps(view)) or "coverage unknown")]
    if _number(cost.get("cap_usd")):
        rows.append(f"{_money(cost['cap_usd'])} accounting cap")
    rows.append("Estimates and routes: /details")
    return summary, rows


def _tokens(tokens: dict, *, indent="") -> list[str]:
    labels = (
        ("input_tokens", "Input"),
        ("output_tokens", "Output"),
        ("cached_input_tokens", "Cache reads (included in input)"),
        ("cache_write_tokens", "Cache writes (included in input)"),
        ("reasoning_output_tokens", "Reasoning (included in output)"),
    )
    return [f"{indent}{label}: {_quantity(tokens.get(key))}" for key, label in labels]


def _route(value) -> str:
    route = _mapping(value)
    parts = [_label(route.get("model"), "model unknown")]
    if route.get("reasoning_effort"):
        parts.append(_label(route["reasoning_effort"]))
    parts.append(_label(route.get("provider"), "provider unreported"))
    if route.get("engine"):
        parts.append(_label(route["engine"]))
    return " · ".join(parts)


def _attempt(row: dict, number: int) -> list[str]:
    route = _mapping(row.get("launch_route"))
    # Historical model/engine fields are still authoritative when an older core
    # has no launch_route. Never fill them from today's saved role settings.
    route = {
        **route,
        "model": route.get("model") or row.get("model"),
        "engine": route.get("engine") or row.get("engine"),
    }
    flags = [name.replace("_", " ") for name in ("active", "rejected", "interrupted", "timed_out") if row.get(name)]
    if row.get("report_only"):
        flags.append("report repair")
    if type(row.get("exit_code")) is int and row["exit_code"]:
        flags.append(f"exit {row['exit_code']}")
    if not flags:
        flags.append("finished" if row.get("finished_at") else "outcome unavailable")
    label = _label(row.get("attempt_id"), f"Attempt {number}")
    job = _label(row.get("job"), "Job unreported")
    worker = f" · worker {_label(row['worker_attempt'])}" if row.get("worker_attempt") else ""
    lines = [f"{label} · {job}{worker} · {', '.join(flags)}", "  " + _route(route)]
    tokens = _mapping(row.get("attributed_tokens")) or _mapping(row.get("tokens"))
    lines.extend(_tokens(tokens, indent="  "))
    if "attributed_reported_usd" in row:
        lines.append("  Provider-reported (attributed): " + _quantity(row["attributed_reported_usd"], money=True))
    elif "reported_cost_usd" in row:
        lines.append("  Provider-reported (attempt observation): " + _money(row["reported_cost_usd"]))
    if "historical_estimated_cost_usd" in row:
        lines.append("  Historical comparison estimate: " + _money(row["historical_estimated_cost_usd"]))
    if row.get("unobserved_usage_possible") or row.get("request_coverage_complete") is False:
        lines.append("  Request coverage incomplete; additional usage may be unreported.")
    return lines


def details(view: dict) -> str:
    """Only core-authored values: alternative totals, attempts and groupings are never added together."""
    if not available(view):
        return ""
    cost, usage, accounting = _mapping(view.get("cost")), _mapping(view.get("usage")), _accounting(view)
    complete = _complete(view)
    lines = [
        "Cost and usage",
        f"Accounted {'total' if complete else 'subtotal'}: {_money(cost.get('spent_usd'))}",
        "Coverage: " + ("complete accounting" if complete else "incomplete; " + (", ".join(_gaps(view)) or "unknown")),
        f"Dated-table estimate: {_money(cost.get('estimated_usd'))}",
    ]
    if cost.get("prices_as_of"):
        lines.append("Price table: " + _label(cost["prices_as_of"]))
    if _number(cost.get("inherited_usd")):
        lines.append("Included from restored checkpoint: " + _money(cost["inherited_usd"]))
    measured = _mapping(accounting.get("cost"))
    lines.append("Provider-reported: " + _quantity(measured.get("reported_usd", cost.get("reported_usd")), money=True))
    if "historical_estimated_usd" in measured:
        lines.append("Historical comparison estimate: " + _quantity(measured["historical_estimated_usd"], money=True))
    lines.append("Estimates and provider reports are not a subscription invoice.")
    if _number(cost.get("cap_usd")):
        lines.append(f"Accounting cap: {_money(cost['cap_usd'])}; incomplete usage cannot establish a billing limit.")
    else:
        lines.append("Accounting cap: not configured or unavailable.")
    breakdown = _mapping(cost.get("breakdown"))
    missing = _mapping(breakdown.get("unpriced"))
    for key, label in (
        ("usage_missing", "missing usage"),
        ("rate_unavailable", "missing rate"),
        ("model_unknown", "unknown model"),
    ):
        if count := _count(missing.get(key)):
            lines.append(f"{count} {label}")
    if billing := _rows(breakdown.get("by_billing")):
        lines.extend(["", "Billing groups (same ledger; not additional spend)"])
        for row in billing:
            kind = _label(row.get("billing"))
            label = "Subscription accounting" if kind == "subscription" else kind.capitalize()
            lines.append(
                f"{label}: {_money(row.get('spent_usd'))} accounted"
                + (" (incomplete)" if row.get("complete") is False else "")
            )
            lines.append("  Provider-reported: " + _money(row.get("reported_usd")))
            estimate = "API-equivalent estimate" if kind == "subscription" else "Dated-table estimate"
            lines.append(f"  {estimate}: {_money(row.get('estimated_usd'))}")
        lines.append(
            "Subscription estimates are not an invoice; quota consumption is unavailable unless reported by core."
        )
        if any(row.get("billing") == "subscription" for row in billing):
            lines.append("Subscription invoice: " + _money(measured.get("subscription_invoice_usd")))
    if groups := _rows(breakdown.get("by_stage")):
        lines.extend(["", "Finished-stage attribution (same ledger; excludes active attempts)"])
        for row in groups:
            gaps = [
                f"{_count(row[key])} {name}"
                for key, name in (
                    ("unpriced_stages", "unpriced"),
                    ("partial_stages", "partial"),
                    ("failed_stages", "failed"),
                    ("interrupted_stages", "interrupted"),
                )
                if _count(row.get(key))
            ]
            lines.append(
                f"{_label(row.get('job'), 'Job unreported')}: {_money(row.get('spent_usd'))}"
                + ("; " + ", ".join(gaps) if gaps else "")
            )
    lines.extend(
        ["", "All-attempt token accounting" if accounting else "Saved-stage tokens; all-attempt coverage unavailable"]
    )
    lines.extend(_tokens(_mapping(accounting.get("tokens")) if accounting else _mapping(usage.get("tokens"))))
    if attempts := _rows(accounting.get("attempts")):
        lines.extend(["", "Actual attempts (already included in core totals)"])
        for number, row in enumerate(attempts, 1):
            lines.extend(_attempt(row, number))
    else:
        lines.append("Actual attempt routes unavailable.")
    routes = _mapping(view.get("routes"))
    if routes:
        lines.extend(["", "Saved routes for future attempts"])
        for route in routes.values():
            if isinstance(route, dict):
                lines.append(_label(route.get("job"), "Job unreported") + ": " + _route(route))
    for key, title in (("escalations", "Escalation receipts"), ("route_assignments", "Route-change receipts")):
        if records := _rows(view.get(key)):
            lines.extend(["", title])
            for row in records:
                before = _route(row["from"]) if isinstance(row.get("from"), dict) else _label(row.get("from"))
                after = _route(row["to"]) if isinstance(row.get("to"), dict) else _label(row.get("to"))
                reason = _label(row.get("trigger") or row.get("via"), "reason unreported").replace("_", " ")
                lines.append(f"{_label(row.get('job'), 'Job unreported')}: {before} → {after}; {reason}")
                if row.get("outcome"):
                    lines.append("  Outcome: " + _label(row["outcome"]).replace("_", " "))
    return "\n".join(lines)
