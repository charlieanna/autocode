"""Where subscription-plan quota went: pool, burn and waste read from the usage ledger (#4229).

A Claude plan and a ChatGPT plan are each a pool of quota that every stage on that tool draws on.
Neither tool publishes its quota in dollars, so the unit here is the stage's API-equivalent price:
the provider's reported cost, else the price table's cost of the recorded tokens. That is a
relative weight between models and stages, not an invoice and not a share of the plan.

Pure functions over public ledger rows (``accounting.attempts``); nothing here launches a model,
reads ``state.json`` or imports a higher layer. An attempt whose usage was never observed is
counted as *unknown* and never as zero, so a pool's total is a floor and says so.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

try:
    from . import autocode_prices as prices
except ImportError:
    import autocode_prices as prices

SCHEMA = 1
POOL_TOOLS = ("claude", "codex")
OTHER = "other"
WINDOWS = {"5h": dt.timedelta(hours=5), "7d": dt.timedelta(days=7)}
DIGITS = 6


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def pool_of(attempt: dict[str, Any]) -> str:
    """The plan pool a stage drew on: ``claude``, ``codex``, or ``other`` for any other route."""
    for value in (
        _dict(attempt.get("execution_identity")).get("provider"),
        _dict(attempt.get("launch_route")).get("provider"),
        attempt.get("engine"),
    ):
        if value in POOL_TOOLS:
            return value
    return OTHER


def _instant(value: Any) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)


def _when(attempt: dict[str, Any]) -> dt.datetime | None:
    return _instant(attempt.get("finished_at")) or _instant(attempt.get("started_at"))


def burn(attempt: dict[str, Any]) -> tuple[float | None, str]:
    """The stage's API-equivalent USD and how it is known: reported, estimated, or unknown (None)."""
    reported = attempt.get("reported_cost_usd")
    if reported is not None:
        return float(reported), "reported"
    recorded = attempt.get("historical_estimated_cost_usd")
    if recorded is not None:
        return float(recorded), "estimated"
    priced = prices.estimate(attempt.get("model"), _dict(attempt.get("tokens")), attempt.get("finished_at"))
    return (priced, "estimated") if priced is not None else (None, "unknown")


def attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Model attempts that could draw quota: not a fake-provider run, not a runner-owned stage."""
    found = []
    for row in rows:
        if row.get("evidence_provenance") == "fake":
            continue
        for item in _dict(row.get("accounting")).get("attempts") or ():
            if isinstance(item, dict) and not item.get("runner_owned"):
                found.append(item)
    return found


def _wasted(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("rejected") or attempt.get("timed_out") or attempt.get("interrupted"))


def _route(attempt: dict[str, Any]) -> tuple[str, str, str]:
    identity = _dict(attempt.get("execution_identity"))
    role = attempt.get("route_role") or attempt.get("role") or identity.get("role") or "unknown"
    effort = attempt.get("reasoning_effort") or identity.get("reasoning_effort") or "default"
    return str(role), str(attempt.get("model") or "unknown"), str(effort)


def _counters() -> dict[str, Any]:
    return {
        "attempts": 0,
        "accepted_attempts": 0,
        "burn_usd": 0.0,
        "unknown_attempts": 0,
        "wasted_usd": 0.0,
        "wasted_attempts": 0,
    }


def _blank_route(key: tuple[str, str, str]) -> dict[str, Any]:
    role, model, effort = key
    return {"role": role, "model": model, "effort": effort, **_counters()}


def _add(entry: dict[str, Any], usd: float | None, wasted: bool) -> None:
    entry["attempts"] += 1
    entry["accepted_attempts"] += not wasted
    entry["wasted_attempts"] += wasted
    if usd is None:
        entry["unknown_attempts"] += 1
        return
    entry["burn_usd"] += usd
    if wasted:
        entry["wasted_usd"] += usd


def _rounded(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: round(value, DIGITS) if isinstance(value, float) else value for key, value in entry.items()}


def summarise(rows: list[dict[str, Any]], *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Burn, waste and rolling-window burn per plan pool, with the routes that drew on each."""
    now = now or dt.datetime.now(dt.UTC)
    pools: dict[str, dict[str, Any]] = {}
    seen = attempts(rows)
    for item in seen:
        usd, _ = burn(item)
        wasted = _wasted(item)
        pool = pools.setdefault(
            pool_of(item),
            {**_counters(), "windows": dict.fromkeys(WINDOWS, 0.0), "routes": {}},
        )
        _add(pool, usd, wasted)
        _add(pool["routes"].setdefault(_route(item), _blank_route(_route(item))), usd, wasted)
        moment = _when(item)
        if usd is not None and moment is not None:
            for name, span in WINDOWS.items():
                if now - span <= moment <= now:
                    pool["windows"][name] += usd
    ordered = {}
    for name in sorted(pools, key=lambda value: (value == OTHER, value)):
        pool = pools[name]
        routes = sorted(
            (_rounded(route) for route in pool["routes"].values()),
            key=lambda route: (-route["burn_usd"], route["role"], route["model"], route["effort"]),
        )
        ordered[name] = {
            **_rounded({key: value for key, value in pool.items() if key not in ("windows", "routes")}),
            "burn_complete": pool["unknown_attempts"] == 0,
            "windows": {key: round(value, DIGITS) for key, value in pool["windows"].items()},
            "routes": routes,
        }
    return {"schema": SCHEMA, "attempts": len(seen), "pools": ordered}
