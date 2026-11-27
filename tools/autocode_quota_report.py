"""The `autocode usage quota` report: where each plan pool's quota went (#4229).

Builds the payload and the text from ledger rows the caller already collected, so this module
depends only on ``autocode_quota_pools`` and never on the usage report that calls it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

try:
    from . import autocode_quota_pools as pools
except ImportError:
    import autocode_quota_pools as pools

SCHEMA = 1
BASIS = (
    "Figures are API-equivalent USD: the provider's reported cost, else the recorded tokens at the price "
    "table. They weigh models and stages against each other; they are not a bill and not a share of the plan."
)
EMPTY = "No live model attempts recorded in usage.jsonl."


def build(
    rows: list[dict[str, Any]],
    *,
    scope: str,
    since: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """The pool burn for ``rows``, with the scope it covers and the runs left out as fake."""
    summary = pools.summarise(rows, now=now)
    return {
        "schema": SCHEMA,
        "scope": scope,
        "since": since,
        "total_runs": len(rows),
        "fake_runs_excluded": sum(1 for row in rows if row.get("evidence_provenance") == "fake"),
        "basis": BASIS,
        "attempts": summary["attempts"],
        "pools": summary["pools"],
    }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _share(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _pool_lines(name: str, pool: dict[str, Any]) -> list[str]:
    unknown = pool["unknown_attempts"]
    floor = ""
    if unknown:
        verb = "has" if unknown == 1 else "have"
        floor = f" (a floor: {_count(unknown, 'attempt')} {verb} no recorded usage)"
    lines = [
        "",
        f"{name} pool: {_count(pool['attempts'], 'attempt')}, ${pool['burn_usd']:.2f} burned{floor}, "
        f"${pool['wasted_usd']:.2f} wasted ({_share(pool['wasted_usd'], pool['burn_usd'])})",
        f"  last 5h ${pool['windows']['5h']:.2f} | last 7d ${pool['windows']['7d']:.2f}",
    ]
    role_width = max([len("role"), *(len(route["role"]) for route in pool["routes"])])
    model_width = max([len("model"), *(len(route["model"]) for route in pool["routes"])])
    effort_width = max([len("effort"), *(len(route["effort"]) for route in pool["routes"])])

    def row(role: str, model: str, effort: str, attempts: str, accepted: str, burn: str, wasted: str) -> str:
        return (
            f"  {role:<{role_width}} {model:<{model_width}} {effort:<{effort_width}} "
            f"{attempts:>8} {accepted:>8} {burn:>10} {wasted:>10}"
        )

    lines.append(row("role", "model", "effort", "attempts", "accepted", "burn", "wasted"))
    for route in pool["routes"]:
        mark = "*" if route["unknown_attempts"] else " "
        lines.append(
            row(
                route["role"],
                route["model"],
                route["effort"],
                str(route["attempts"]),
                str(route["accepted_attempts"]),
                f"${route['burn_usd']:.2f}{mark}",
                f"${route['wasted_usd']:.2f}",
            )
        )
    return lines


def format_text(payload: dict[str, Any]) -> str:
    """Render ``build`` as the pool lines a person reads."""
    since = f", since: {payload['since']}" if payload.get("since") else ""
    lines = [
        f"Quota pool report (scope: {payload['scope']}{since}, runs: {payload['total_runs']}, "
        f"attempts: {payload['attempts']})",
        payload["basis"],
    ]
    if payload["fake_runs_excluded"]:
        lines.append(f"{_count(payload['fake_runs_excluded'], 'fake-provider run')} left out: they use no plan quota.")
    if not payload["pools"]:
        lines.append(EMPTY)
        return "\n".join(lines)
    for name, pool in payload["pools"].items():
        lines.extend(_pool_lines(name, pool))
    if any(route["unknown_attempts"] for pool in payload["pools"].values() for route in pool["routes"]):
        lines.extend(["", "* some attempts have no recorded usage, so that burn is a floor."])
    return "\n".join(lines)
