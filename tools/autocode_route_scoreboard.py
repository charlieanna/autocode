"""How each model did at each job on real runs, counted only from verified work (#4251, step 1 of restarting #2049).

``autocode usage quota`` counts an attempt as accepted when the runner took it, even when the next stage then
sent the work back. This scoreboard counts what survived. Read only: it suggests nothing and changes no route.

What counts (one rule, read from the ledger rows alone, never from ``state.json``):

- **A run counts** only when its row is live evidence (``evidence_provenance`` ``live``) and the run completed,
  which means the completion gate passed. Fake, mixed and undeclared runs, and runs that paused, are left out
  and counted by reason. A ledger row is rewritten as its run moves on, so a run that later stops being
  complete stops counting.
- **An attempt counts** when a model made it: runner-owned stages, a saved run's speculative pass and a stage
  still running are left out. A report repair is counted apart; the attempt it repaired is already rejected.
- **Sent back** is read from the order of the run's attempts, for the two jobs whose work a later stage takes
  up and can return:

  - a Builder attempt is sent back when a later Builder attempt works on the same task, whatever sent it
    (the regression proof, the Tester, the Completion Reviewer or a stronger Builder);
  - a Planner attempt is sent back when another Planner attempt follows it before any Builder starts, so a
    plan the Plan Reviewer or the person returned counts, and a follow-up request after building does not.

  Every other job's answer is judged only on whether the runner took it: whether a review was right is not
  observable from the ledger, and the scoreboard says so instead of guessing.
- **First time right** is an attempt that was accepted and not sent back. Its rate is over the attempts that
  were not report repairs, with a 95% Wilson range, so a handful of attempts shows how little it proves.

Plan size (small, medium, large, unknown) splits each route's counts, because a model given harder tasks
looks worse on the same rate. The figures in USD are API-equivalent burn (``autocode_quota_pools.burn``).

Pure functions over ledger rows; imports only ``autocode_quota_pools`` and ``autocode_names``.
"""

from __future__ import annotations

import math
from typing import Any

try:
    from . import autocode_names as names
    from . import autocode_quota_pools as pools
except ImportError:
    import autocode_names as names
    import autocode_quota_pools as pools

SCHEMA = 1
LIVE = "live"
COMPLETED = "completed"
JUDGED_BY_NEXT_STAGE = ("Builder", "Planner")
PLAN_SIZES = ("small", "medium", "large", "unknown")
Z_95 = 1.959963984540054
DIGITS = 6
BASIS = (
    "Only live runs that completed count. A Builder attempt is sent back when a later Builder attempt works on "
    "the same task; a Planner attempt when another Planner attempt follows before building starts. Other jobs "
    "are judged only on whether the runner took the answer. USD is API-equivalent burn, not a bill."
)
EMPTY = "No live, completed runs in usage.jsonl yet, so nothing is counted."


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def wilson(successes: int, trials: int) -> tuple[float, float] | None:
    """The 95% Wilson score range of ``successes`` in ``trials``, or None for no trials."""
    if trials <= 0:
        return None
    share = successes / trials
    denominator = 1 + Z_95 * Z_95 / trials
    centre = share + Z_95 * Z_95 / (2 * trials)
    spread = Z_95 * math.sqrt(share * (1 - share) / trials + Z_95 * Z_95 / (4 * trials * trials))
    return (max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator))


def exclusion(row: dict[str, Any]) -> str | None:
    """Why a ledger row does not count, or None when it does."""
    if row.get("evidence_provenance") != LIVE:
        return "not_live"
    if _dict(row.get("outcome")).get("state") != COMPLETED:
        return "not_completed"
    if not isinstance(_dict(row.get("accounting")).get("attempts"), list):
        return "no_attempts"
    return None


def _is_repair(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("original_stage")) or str(attempt.get("stage") or "").endswith("_report_repair")


def _failed(attempt: dict[str, Any]) -> bool:
    return bool(attempt.get("rejected") or attempt.get("timed_out") or attempt.get("interrupted"))


def model_attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The row's model attempts in the order they started; runner stages, speculative and running ones left out."""
    found = [
        item
        for item in _dict(row.get("accounting")).get("attempts") or ()
        if isinstance(item, dict)
        and not item.get("runner_owned")
        and not item.get("speculative")
        and not item.get("active")
    ]
    if all(isinstance(item.get("started_at"), str) for item in found):
        found = sorted(found, key=lambda item: item["started_at"])
    return found


def _job(attempt: dict[str, Any]) -> str:
    job = attempt.get("job")
    if isinstance(job, str) and job:
        return job
    return names.stage(str(attempt.get("original_stage") or attempt.get("stage") or "unknown"))


def sent_back(attempts: list[dict[str, Any]]) -> list[bool | None]:
    """For each attempt in order: True when a later stage sent its work back, False when not, None when unjudged."""
    marks: list[bool | None] = []
    for index, attempt in enumerate(attempts):
        job = _job(attempt)
        if job not in JUDGED_BY_NEXT_STAGE or _is_repair(attempt) or _failed(attempt):
            marks.append(None)
            continue
        later = [item for item in attempts[index + 1 :] if not _is_repair(item)]
        if job == "Builder":
            task = attempt.get("task_id")
            marks.append(bool(task) and any(_job(item) == "Builder" and item.get("task_id") == task for item in later))
            continue
        returned = False
        for item in later:
            if _job(item) == "Builder":
                break
            if _job(item) == "Planner":
                returned = True
                break
        marks.append(returned)
    return marks


def _backend(attempt: dict[str, Any]) -> str:
    for value in (
        _dict(attempt.get("execution_identity")).get("provider"),
        _dict(attempt.get("launch_route")).get("provider"),
        attempt.get("engine"),
    ):
        if isinstance(value, str) and value:
            return value
    return "unknown"


def route_key(attempt: dict[str, Any]) -> tuple[str, str, str, str]:
    """``(backend, job, model, effort)``: the route one attempt ran on, as the scoreboard groups it."""
    identity = _dict(attempt.get("execution_identity"))
    effort = attempt.get("reasoning_effort") or identity.get("reasoning_effort") or "default"
    return _backend(attempt), _job(attempt), str(attempt.get("model") or "unknown"), str(effort)


def _counts() -> dict[str, Any]:
    return {"attempts": 0, "first_time": 0, "sent_back": 0, "rejected": 0}


def _blank(key: tuple[str, str, str, str]) -> dict[str, Any]:
    backend, job, model, effort = key
    return {
        "backend": backend,
        "job": job,
        "model": model,
        "effort": effort,
        "judged_by": "next_stage" if job in JUDGED_BY_NEXT_STAGE else "answer_only",
        **_counts(),
        "report_repairs": 0,
        "runs": 0,
        "burn_usd": 0.0,
        "unknown_burn_attempts": 0,
        "by_plan_size": {},
    }


def _finish(entry: dict[str, Any]) -> dict[str, Any]:
    trials = entry["attempts"]
    found = wilson(entry["first_time"], trials)
    rate = round(entry["first_time"] / trials, DIGITS) if trials else None
    runs = entry.pop("_runs")
    return {
        **entry,
        "sent_back": entry["sent_back"] if entry["judged_by"] == "next_stage" else None,
        "first_time_rate": rate,
        "first_time_range": [round(found[0], DIGITS), round(found[1], DIGITS)] if found else None,
        "runs": len(runs),
        "burn_usd": round(entry["burn_usd"], DIGITS),
        "usd_per_run": round(entry["burn_usd"] / len(runs), DIGITS) if runs else None,
        "burn_complete": entry["unknown_burn_attempts"] == 0,
        "by_plan_size": {size: entry["by_plan_size"][size] for size in PLAN_SIZES if size in entry["by_plan_size"]},
    }


def build(rows: list[dict[str, Any]], *, scope: str = "workspace", since: str | None = None) -> dict[str, Any]:
    """The scoreboard over ``rows``: one entry per route, and the runs left out by reason."""
    excluded: dict[str, int] = {}
    routes: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    counted = 0
    for row in rows:
        reason = exclusion(row)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        counted += 1
        size = _dict(row.get("outcome")).get("plan_size")
        size = size if size in PLAN_SIZES else "unknown"
        attempts = model_attempts(row)
        for attempt, returned in zip(attempts, sent_back(attempts), strict=True):
            key = route_key(attempt)
            entry = routes.get(key)
            if entry is None:
                entry = routes[key] = {**_blank(key), "_runs": set()}
            entry["_runs"].add(str(row.get("run")))
            usd, _basis = pools.burn(attempt)
            if usd is None:
                entry["unknown_burn_attempts"] += 1
            else:
                entry["burn_usd"] += usd
            if _is_repair(attempt):
                entry["report_repairs"] += 1
                continue
            split = entry["by_plan_size"].setdefault(size, _counts())
            for target in (entry, split):
                target["attempts"] += 1
                if _failed(attempt):
                    target["rejected"] += 1
                elif returned:
                    target["sent_back"] += 1
                else:
                    target["first_time"] += 1
    ordered = sorted(
        (_finish(entry) for entry in routes.values()),
        key=lambda entry: (entry["backend"], entry["job"], -entry["attempts"], entry["model"], entry["effort"]),
    )
    return {
        "schema": SCHEMA,
        "scope": scope,
        "since": since,
        "basis": BASIS,
        "total_runs": len(rows),
        "counted_runs": counted,
        "excluded_runs": dict(sorted(excluded.items())),
        "routes": ordered,
    }


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{round(100 * value)}%"


def format_text(payload: dict[str, Any]) -> str:
    """Render ``build`` as the table a person reads."""
    since = f", since: {payload['since']}" if payload.get("since") else ""
    lines = [
        f"Route scoreboard (scope: {payload['scope']}{since}, runs counted: {payload['counted_runs']} "
        f"of {payload['total_runs']})",
        payload["basis"],
    ]
    left_out = payload.get("excluded_runs") or {}
    if left_out:
        words = {"not_live": "not live", "not_completed": "not completed", "no_attempts": "no attempt records"}
        lines.append("Left out: " + ", ".join(f"{count} {words.get(key, key)}" for key, count in left_out.items()))
    if not payload["routes"]:
        lines.append(EMPTY)
        return "\n".join(lines)
    header = (
        "backend",
        "job",
        "model",
        "effort",
        "attempts",
        "first",
        "back",
        "rejected",
        "rate",
        "95% range",
        "$/run",
    )
    table = [header]
    for entry in payload["routes"]:
        found = entry["first_time_range"]
        table.append(
            (
                entry["backend"],
                entry["job"],
                entry["model"],
                entry["effort"],
                str(entry["attempts"]),
                str(entry["first_time"]),
                "-" if entry["sent_back"] is None else str(entry["sent_back"]),
                str(entry["rejected"]),
                _percent(entry["first_time_rate"]),
                f"{_percent(found[0])}-{_percent(found[1])}" if found else "-",
                "-"
                if entry["usd_per_run"] is None
                else f"${entry['usd_per_run']:.2f}{'' if entry['burn_complete'] else '*'}",
            )
        )
    widths = [max(len(line[column]) for line in table) for column in range(len(header))]
    for line in table:
        cells = [
            cell.ljust(widths[column]) if column < 4 else cell.rjust(widths[column]) for column, cell in enumerate(line)
        ]
        lines.append("  " + " ".join(cells).rstrip())
    lines.append("")
    lines.append(
        "first: accepted and not sent back; back: sent back by a later stage ('-' where that is not observable); "
        "rate: first over attempts, report repairs not included."
    )
    if any(not entry["burn_complete"] for entry in payload["routes"]):
        lines.append("* some attempts have no recorded usage, so that $/run is a floor.")
    return "\n".join(lines)
