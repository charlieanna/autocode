"""How much of each run's model cost is known, by run, stage and model (#4239).

Pure functions over public ledger rows (``accounting.attempts`` in ``usage.jsonl``); nothing here
launches a model, reads ``state.json`` or imports a higher layer. Every amount comes from the
same answer the quota report reads (``autocode_quota_pools.burn``): the provider's reported
charge, else an API-equivalent estimate from the price table, else unknown. The three are kept
apart. An unknown amount is never counted as zero, so a total is given only when every model
stage it covers was priced and its completion event arrived; otherwise only the known part is
given, as a lower bound.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

try:
    from . import autocode_quota_pools as quota_pools
except ImportError:
    import autocode_quota_pools as quota_pools

SCHEMA = 1
DIGITS = 6
# The ledger writer (autocode_usage) records these two texts; this module reads them back.
NO_FINISH_ISSUE = "No native request finishes; unobserved usage is unknown"
CUT_STREAM_ISSUE = "The stage's event stream was cut; its usage is a lower bound"
EVENTS: tuple[str, ...] = ("complete", "truncated", "missing", "running")
COVERAGE: tuple[str, ...] = ("complete", "partial", "unknown", "no_model_attempts", "not_recorded")
TERMINAL: tuple[str, ...] = ("completed", "failed", "rejected", "timed_out", "interrupted", "running", "unknown")
RECEIPTS: tuple[str, ...] = ("recorded", "none_named", "not_recorded")
DELIVERED = "completed"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, DIGITS)


def _percentile(values: list[float], pct: float) -> float:
    # Nearest rank, as the usage report computes it.
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))]


def _stats(values: list[float], digits: int) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "p90": None}
    return {
        "count": len(values),
        "median": round(float(statistics.median(values)), digits),
        "p90": round(float(_percentile(values, 0.90)), digits),
    }


def terminal_status(attempt: dict[str, Any]) -> str:
    """How the stage ended, as the runner recorded it; ``unknown`` when it recorded nothing."""
    if attempt.get("active"):
        return "running"
    if attempt.get("timed_out"):
        return "timed_out"
    if attempt.get("interrupted"):
        return "interrupted"
    code = attempt.get("exit_code")
    if type(code) is int and code != 0:
        return "failed"
    if attempt.get("rejected"):
        return "rejected"
    return "completed" if code == 0 and type(code) is int else "unknown"


def completion_event(attempt: dict[str, Any]) -> str:
    """Whether the provider's final usage for the stage arrived: complete, truncated, missing or running."""
    if attempt.get("active"):
        return "running"
    issues = [str(issue) for issue in attempt.get("issues") or ()]
    unfinished = attempt.get("unfinished_requests")
    if (
        attempt.get("usage_truncated") is True
        or (type(unfinished) is int and unfinished > 0)
        or CUT_STREAM_ISSUE in issues
    ):
        return "truncated"
    observed = any(value is not None for value in _dict(attempt.get("tokens")).values())
    if NO_FINISH_ISSUE in issues or (attempt.get("reported_cost_usd") is None and not observed):
        return "missing"
    return "complete"


def _receipt(attempt: dict[str, Any]) -> str:
    if "provider_request_ids" not in attempt:
        return "not_recorded"
    ids = attempt.get("provider_request_ids")
    return "recorded" if isinstance(ids, list) and ids else "none_named"


def _resolved(attempt: dict[str, Any]) -> list[str]:
    value = attempt.get("resolved_models")
    return [str(item) for item in value if item] if isinstance(value, list) else []


def _uncertain(attempt: dict[str, Any]) -> bool:
    # The ledger's own flag that some usage may be unobserved, from any recorded accounting issue.
    return attempt.get("unobserved_usage_possible") is True or bool(attempt.get("issues"))


def _measure(attempt: dict[str, Any]) -> dict[str, Any]:
    usd, basis = quota_pools.burn(attempt)
    event = completion_event(attempt)
    return {
        "attempt": attempt,
        "usd": usd,
        "basis": basis,
        "event": event,
        "status": terminal_status(attempt),
        "covered": basis != "unknown" and event == "complete" and not _uncertain(attempt),
        "uncertain": event == "complete" and _uncertain(attempt),
        "receipt": _receipt(attempt),
        "resolved": _resolved(attempt),
        "seconds": _number(attempt.get("duration_seconds")),
    }


def _money(items: list[dict[str, Any]]) -> dict[str, Any]:
    reported = [item["usd"] for item in items if item["basis"] == "reported"]
    estimated = [item["usd"] for item in items if item["basis"] == "estimated"]
    known = reported + estimated
    complete = bool(items) and all(item["covered"] for item in items)
    known_usd = _round(sum(known)) if known else None
    return {
        "reported_usd": _round(sum(reported)) if reported else None,
        "reported_attempts": len(reported),
        "estimated_usd": _round(sum(estimated)) if estimated else None,
        "estimated_attempts": len(estimated),
        "unknown_attempts": sum(item["basis"] == "unknown" for item in items),
        "known_usd": known_usd,
        "total_usd": known_usd if complete else None,
        "complete": complete,
    }


def _events(items: list[dict[str, Any]]) -> dict[str, int]:
    return {event: sum(item["event"] == event for item in items) for event in EVENTS}


def _receipts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {kind: sum(item["receipt"] == kind for item in items) for kind in RECEIPTS}
    return {**counts, "resolved_model": sum(bool(item["resolved"]) for item in items)}


def _model(attempt: dict[str, Any]) -> str:
    return str(attempt.get("model") or "unknown")


def _stage(attempt: dict[str, Any]) -> str:
    return str(attempt.get("stage") or "unknown")


def _run(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    outcome = _dict(row.get("outcome"))
    accounting = row.get("accounting")
    items = []
    if isinstance(accounting, dict):
        for attempt in accounting.get("attempts") or ():
            if isinstance(attempt, dict) and not attempt.get("runner_owned"):
                items.append(_measure(attempt))
    money = _money(items)
    if not isinstance(accounting, dict):
        coverage = "not_recorded"
    elif not items:
        coverage = "no_model_attempts"
    elif money["complete"]:
        coverage = "complete"
    elif money["known_usd"] is None:
        coverage = "unknown"
    else:
        coverage = "partial"
    entry = {
        "run": row.get("run"),
        "workflow": str(row.get("workflow") or "unknown"),
        "state": outcome.get("state"),
        "plan_size": outcome.get("plan_size"),
        "wall_seconds": _number(outcome.get("wall_seconds")),
        "attempts": len(items),
        "coverage": coverage,
        **money,
        "completion_events": _events(items),
    }
    return entry, items


def _by_stage(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(_stage(item["attempt"]), []).append(item)
    result = []
    for stage in sorted(groups):
        group = groups[stage]
        seconds = [item["seconds"] for item in group if item["seconds"] is not None]
        result.append(
            {
                "stage": stage,
                "job": next((item["attempt"].get("job") for item in group if item["attempt"].get("job")), None),
                "attempts": len(group),
                "models": sorted({_model(item["attempt"]) for item in group}),
                **_money(group),
                "seconds": {
                    "known": round(sum(seconds), 3) if seconds else None,
                    "unrecorded_attempts": len(group) - len(seconds),
                },
                "completion_events": _events(group),
                "receipts": _receipts(group),
            }
        )
    return result


def _by_model(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(_model(item["attempt"]), []).append(item)
    result = []
    for model in sorted(groups):
        group = groups[model]
        resolved = sorted({name for item in group for name in item["resolved"]})
        result.append(
            {
                "model": model,
                "attempts": len(group),
                **_money(group),
                "completion_events": _events(group),
                "receipts": _receipts(group),
                # The model names the provider reported serving; None when no stage recorded one.
                "resolved_models": resolved or None,
            }
        )
    return result


def _delivered(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault(run["workflow"], []).append(run)
    result = []
    for workflow in sorted(groups):
        group = groups[workflow]
        delivered = [run for run in group if run["state"] == DELIVERED]
        priced = [run["total_usd"] for run in delivered if run["coverage"] == "complete"]
        walls = [run["wall_seconds"] for run in delivered if run["wall_seconds"] is not None]
        incomplete = sum(run["coverage"] != "complete" for run in group)
        spent = [run["total_usd"] for run in group]
        result.append(
            {
                "workflow": workflow,
                "runs": len(group),
                "delivered_runs": len(delivered),
                "incomplete_runs": incomplete,
                # Only delivered runs whose every model stage was priced; the rest are counted, not guessed.
                "usd_per_delivered_run": {
                    "priced_runs": len(priced),
                    "excluded_runs": len(delivered) - len(priced),
                    **{key: value for key, value in _stats(priced, DIGITS).items() if key != "count"},
                    "total": _round(sum(priced)) if priced else None,
                },
                "wall_seconds_per_delivered_run": {
                    **_stats(walls, 3),
                    "unrecorded_runs": len(delivered) - len(walls),
                },
                # Everything the kind spent, failed runs included, per delivery; only when every run's cost is known.
                "usd_per_delivery": _round(sum(spent) / len(delivered)) if delivered and not incomplete else None,
            }
        )
    return result


def _matched(rows_items: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for run, items in rows_items:
        size = run["plan_size"]
        if not isinstance(size, str) or not size:
            continue
        for item in items:
            groups.setdefault((_stage(item["attempt"]), size), {}).setdefault(_model(item["attempt"]), []).append(item)
    result = []
    for stage, size in sorted(groups):
        models = groups[(stage, size)]
        if len(models) < 2:
            continue
        entries = []
        for model in sorted(models):
            group = models[model]
            entries.append(
                {
                    "model": model,
                    "attempts": len(group),
                    "completed_attempts": sum(item["status"] == "completed" for item in group),
                    "priced_attempts": sum(item["covered"] for item in group),
                    "usd_per_attempt": _stats([item["usd"] for item in group if item["covered"]], DIGITS),
                    "seconds_per_attempt": _stats(
                        [item["seconds"] for item in group if item["seconds"] is not None], 3
                    ),
                }
            )
        first = [item for group in models.values() for item in group]
        result.append(
            {
                "stage": stage,
                "job": next((item["attempt"].get("job") for item in first if item["attempt"].get("job")), None),
                "plan_size": size,
                "models": entries,
            }
        )
    return result


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cost coverage of ledger rows: per run, stage and model, per delivered run, and on matched workloads."""
    rows_items = [_run(row) for row in rows if isinstance(row, dict)]
    runs = [run for run, _ in rows_items]
    items = [item for _, group in rows_items for item in group]
    return {
        "schema": SCHEMA,
        "scope": (
            "Model stages only; provider-reported charges, API-equivalent estimates and unknown amounts are kept "
            "apart, and an unknown amount is never zero"
        ),
        "attempts": len(items),
        "usd": _money(items),
        "runs": {kind: sum(run["coverage"] == kind for run in runs) for kind in COVERAGE},
        "completion_events": _events(items),
        # Stages whose completion arrived but whose ledger row records an accounting issue: never counted as covered.
        "uncertain_attempts": sum(item["uncertain"] for item in items),
        "terminal_status": {status: sum(item["status"] == status for item in items) for status in TERMINAL},
        "receipts": _receipts(items),
        "by_run": runs,
        "by_stage": _by_stage(items),
        "by_model": _by_model(items),
        "delivered": _delivered(runs),
        "matched_workloads": _matched(rows_items),
    }
