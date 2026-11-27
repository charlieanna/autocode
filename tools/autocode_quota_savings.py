"""The cheaper-route what-if for a plan pool, read only from its own ledger evidence (#4229).

Inside one plan (a Claude pool or a Codex pool) the same stage can run on several routes: a model at a
reasoning level. A route is a substitute for a costlier one in the same pool and role only when the
ledger itself shows it did that role, enough times to count, at an accept rate at least as good and a
lower price per attempt. Nothing here consults a model table, so it names only what was observed, and
it would never move a checker onto the model whose work it checks (``autocode_verifier_independence``).

The figures are a what-if: accepted means the runner took the attempt, not that the task was judged
well done. Pure functions over ``autocode_quota_pools.summarise`` output; nothing here changes a route.
"""

from __future__ import annotations

from typing import Any, cast

try:
    from . import autocode_names as names
    from . import autocode_quota_pools as pools
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_names as names
    import autocode_quota_pools as pools
    import autocode_verifier_independence as independence

SCHEMA = 1
MIN_SAMPLE = 5
BASIS = (
    "This is a what-if from your own ledger. A route counts as a cheaper substitute only when the same pool "
    f"and role ran it at least {MIN_SAMPLE} priced times, accepted at least as often, at a lower price per "
    "attempt, and it would not check its own model's work. Accepted means the runner took the attempt, not "
    "that the task was judged well done, and different tasks differ. AutoCode does not change any route."
)


def _priced(route: dict[str, Any]) -> int:
    return route["attempts"] - route["unknown_attempts"]


def _per_attempt(route: dict[str, Any]) -> float | None:
    priced = _priced(route)
    return route["burn_usd"] / priced if priced else None


def _counterparts(role: str) -> tuple[str, ...]:
    """The roles whose models must stay independent of ``role``'s model."""
    found = []
    for producer, verifier, _label in independence.PAIRS:
        if role == producer:
            found.append(verifier)
        elif role == verifier:
            found.append(producer)
    return tuple(found)


def _models_by_role(pool_summaries: dict[str, Any]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for pool in pool_summaries.values():
        for route in pool["routes"]:
            found.setdefault(names.route(route["role"]), set()).add(route["model"])
    return found


def _independent(candidate: dict[str, Any], by_role: dict[str, set[str]]) -> bool:
    for counterpart in _counterparts(names.route(candidate["role"])):
        for model in by_role.get(counterpart, ()):
            if independence.clash(candidate["model"], model, vendor_engine=True):
                return False
    return True


def _evidence(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": route["model"],
        "effort": route["effort"],
        "attempts": route["attempts"],
        "accepted_attempts": route["accepted_attempts"],
        "priced_attempts": _priced(route),
        "usd_per_attempt": round(_per_attempt(route) or 0.0, pools.DIGITS),
    }


def _qualifies(candidate: dict[str, Any], base: dict[str, Any], by_role: dict[str, set[str]]) -> bool:
    if _priced(candidate) < MIN_SAMPLE:
        return False
    cheaper, current = _per_attempt(candidate), _per_attempt(base)
    if cheaper is None or current is None or cheaper >= current:
        return False
    # accepted/attempts at least as high, compared without dividing
    if candidate["accepted_attempts"] * base["attempts"] < base["accepted_attempts"] * candidate["attempts"]:
        return False
    return _independent(candidate, by_role)


def _opportunity(base: dict[str, Any], routes: list[dict[str, Any]], by_role: dict[str, set[str]]):
    found = [route for route in routes if route is not base and _qualifies(route, base, by_role)]
    if not found:
        return None
    best = min(
        found,
        key=lambda route: (
            _per_attempt(route),
            -route["accepted_attempts"] / route["attempts"],
            route["model"],
            route["effort"],
        ),
    )
    # Both are priced: the caller passes only a priced base, and _qualifies admits only a priced candidate.
    saving = (cast(float, _per_attempt(base)) - cast(float, _per_attempt(best))) * _priced(base)
    return {
        "role": base["role"],
        "from": _evidence(base),
        "to": _evidence(best),
        "baseline_usd": round(base["burn_usd"], pools.DIGITS),
        "saving_usd": round(saving, pools.DIGITS),
    }


def _pool_opportunities(pool: dict[str, Any], by_role: dict[str, set[str]]) -> list[dict[str, Any]]:
    by_job: dict[str, list[dict[str, Any]]] = {}
    for route in pool["routes"]:
        by_job.setdefault(route["role"], []).append(route)
    found = []
    for routes in by_job.values():
        for base in routes:
            if _priced(base) and (opportunity := _opportunity(base, routes, by_role)):
                found.append(opportunity)
    return sorted(found, key=lambda item: (-item["saving_usd"], item["role"], item["from"]["model"]))


def suggest(pool_summaries: dict[str, Any]) -> dict[str, Any]:
    """Per plan pool, the cheaper routes its own ledger proves and what each would have saved."""
    by_role = _models_by_role(pool_summaries)
    result: dict[str, Any] = {}
    total = 0.0
    for name in pools.POOL_TOOLS:
        pool = pool_summaries.get(name)
        if not pool:
            continue
        found = _pool_opportunities(pool, by_role)
        saving = sum(item["saving_usd"] for item in found)
        total += saving
        result[name] = {"opportunities": found, "saving_usd": round(saving, pools.DIGITS)}
    return {
        "schema": SCHEMA,
        "basis": BASIS,
        "min_attempts": MIN_SAMPLE,
        "pools": result,
        "saving_usd": round(total, pools.DIGITS),
    }
