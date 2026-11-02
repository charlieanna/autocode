"""What each stage of a run did and what it used, for the full-screen chat: a display of the
public status view's own attempt and cost records, never a route choice or a spend decision.

Every figure shown is a value the core wrote: an attempt's route, start, duration, outcome and
attributed tokens (``usage.accounting.attempts``), a stage's accounted spend
(``cost.breakdown.by_stage``) and the run's accounted total (``cost.spent_usd``). The only
arithmetic is adding one stage's own attempt tokens and durations together, and adding separate
chats' run totals for the session line; those are distinct ledgers, so nothing is counted twice,
and a total built from an incomplete part says so.
"""

from __future__ import annotations

import math
from datetime import datetime

DONE, WORKING, FAILED, RETRYING = "✓", "●", "✗", "↻"
ASCII_MARKS = {DONE: "+", WORKING: "*", FAILED: "x", RETRYING: "~"}


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def money(value) -> str:
    """A dollar amount as the panel shows it: cents for a dollar or more, three figures below."""
    if not _number(value):
        return "$?"
    if value == 0:
        return "$0"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.3g}" if value >= 0.001 else "<$0.001"


def tokens(value) -> str:
    """A token count in a few characters: 950, 1.2k, 184k, 3.4M."""
    if not _number(value):
        return "?"
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= size:
            shown = value / size
            return (f"{shown:.1f}" if shown < 10 else f"{shown:.0f}") + suffix
    return f"{value:.0f}"


def duration(seconds) -> str:
    if not _number(seconds):
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _known(quantity) -> tuple[float | None, bool]:
    """An accounting quantity's known amount and whether it is complete; a bare number is complete."""
    if isinstance(quantity, dict):
        amount = quantity.get("value") if quantity.get("complete") is True else quantity.get("known")
        return (amount if _number(amount) else None), quantity.get("complete") is True
    return (quantity if _number(quantity) else None), _number(quantity)


def _started(row: dict) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(row.get("started_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _seconds(row: dict, now: datetime | None) -> float | None:
    if _number(row.get("duration_seconds")):
        return float(row["duration_seconds"])
    started = _started(row)
    if row.get("active") and started is not None and now is not None:
        return max(0.0, (now - started).total_seconds())
    return None


def _failed(row: dict) -> bool:
    exit_code = row.get("exit_code")
    return bool(
        row.get("rejected")
        or row.get("interrupted")
        or row.get("timed_out")
        or (type(exit_code) is int and exit_code != 0)
    )


def _why(row: dict) -> str:
    for key, words in (
        ("rejected", "answer rejected"),
        ("timed_out", "timed out"),
        ("interrupted", "interrupted"),
    ):
        if row.get(key):
            return words
    exit_code = row.get("exit_code")
    return f"exit {exit_code}" if type(exit_code) is int and exit_code else ""


def _route(row: dict) -> str:
    route = {**_mapping(row.get("launch_route"))}
    model = route.get("model") or row.get("model")
    effort = route.get("reasoning_effort") or row.get("reasoning_effort")
    return " ".join(str(part) for part in (model, effort) if isinstance(part, str) and part.strip())


def stages(view: dict | None, now: datetime | None = None) -> list[dict]:
    """One row per stage the run has launched, in the order it first ran.

    Each row: ``job`` (the screen name), ``route`` (model and reasoning level of its newest
    attempt), ``state`` (working, done, failed or retrying), ``attempts``, ``seconds``,
    ``tokens`` (input and output known so far, and whether complete), ``spent_usd`` and why
    its newest attempt did not succeed, when it did not.
    """
    view = _mapping(view)
    attempts = _rows(_mapping(_mapping(view.get("usage")).get("accounting")).get("attempts"))
    spend = {
        row.get("stage"): row for row in _rows(_mapping(_mapping(view.get("cost")).get("breakdown")).get("by_stage"))
    }
    grouped: dict[str, list[dict]] = {}
    for row in attempts:
        # A report repair (``sol_report_repair``) is another attempt at the stage it repairs.
        stage = row.get("original_stage") or row.get("stage")
        if isinstance(stage, str) and stage:
            grouped.setdefault(stage, []).append(row)
    result = []
    for stage, rows in grouped.items():
        newest = rows[-1]
        if newest.get("active"):
            state = RETRYING if any(_failed(row) for row in rows[:-1]) else WORKING
        elif _failed(newest):
            state = FAILED
        else:
            state = DONE
        seconds = [value for row in rows if (value := _seconds(row, now)) is not None]
        counted = {"input": 0.0, "output": 0.0}
        complete = True
        for row in rows:
            used = _mapping(row.get("attributed_tokens")) or _mapping(row.get("tokens"))
            for side in counted:
                amount, whole = _known(used.get(f"{side}_tokens"))
                counted[side] += amount or 0
                complete = complete and whole
        # A repaired stage's cost is its own stage row and its repair's: separate stages of one ledger.
        costs: list[float] = []
        for name in dict.fromkeys(str(row.get("stage")) for row in rows):
            value = _mapping(spend.get(name)).get("spent_usd")
            if isinstance(value, int | float) and _number(value):
                costs.append(float(value))
        # A stage on a later attempt says why the one before it did not succeed.
        previous = _why(rows[-2]) if newest.get("active") and len(rows) > 1 else ""
        result.append(
            {
                "stage": stage,
                "job": str(newest.get("job") or stage),
                "route": _route(newest),
                "state": state,
                "attempts": len(rows),
                "failed_attempts": sum(_failed(row) for row in rows),
                "seconds": sum(seconds) if seconds else None,
                "input_tokens": counted["input"],
                "output_tokens": counted["output"],
                "tokens_complete": complete,
                "spent_usd": sum(costs) if costs else None,
                "why": _why(newest) if _failed(newest) else previous,
            }
        )
    return result


def stage_lines(row: dict, *, unicode: bool = True) -> tuple[str, str, str]:
    """A stage as two panel lines and the style of the first: what it is and how it went,
    then the model it ran on and what it used."""
    mark = row["state"] if unicode else ASCII_MARKS[row["state"]]
    head = [row["job"]]
    if row["seconds"] is not None:
        head.append(duration(row["seconds"]))
    if row["spent_usd"] is not None and (row["spent_usd"] or row["route"]):  # a stage with no model costs nothing
        head.append(money(row["spent_usd"]))
    if row["attempts"] > 1:
        head.append(f"attempt {row['attempts']}")
    detail = [row["route"]] if row["route"] else []
    used = row["input_tokens"] + row["output_tokens"]
    if used:
        detail.append(f"{tokens(used)}{'' if row['tokens_complete'] else '+'} tokens")
    if row["why"] and row["state"] != DONE:
        detail.append(row["why"])
    style = {DONE: "good", WORKING: "accent", FAILED: "bad", RETRYING: "warn"}[row["state"]]
    return f"{mark} {' · '.join(head)}", "  " + " · ".join(detail), style


def struggling(view: dict | None, now: datetime | None = None) -> str:
    """For the working stage, when it is on its second attempt or later: which attempt, how
    long it has run and why the previous one did not succeed. Empty when nothing is wrong."""
    working = [row for row in stages(view, now) if row["state"] in (WORKING, RETRYING)]
    if not working or working[-1]["attempts"] < 2:
        return ""
    row = working[-1]
    parts = [row["job"], f"attempt {row['attempts']}"]
    if row["seconds"] is not None:
        parts.append(duration(row["seconds"]))
    if row["why"]:
        parts.append(f"last one {row['why']}")
    return " · ".join(parts)


def run_total(view: dict | None) -> dict | None:
    """The run's own accounted spend and token totals, as the core wrote them; None before any."""
    view = _mapping(view)
    cost = _mapping(view.get("cost"))
    accounting = _mapping(_mapping(view.get("usage")).get("accounting"))
    used = _mapping(accounting.get("tokens"))
    spent = cost.get("spent_usd") if _number(cost.get("spent_usd")) else None
    inherited = cost.get("inherited_usd") if _number(cost.get("inherited_usd")) else 0
    counted = {side: _known(used.get(f"{side}_tokens")) for side in ("input", "output")}
    if spent is None and not any(amount for amount, _ in counted.values()):
        return None
    return {
        "spent_usd": spent,
        "inherited_usd": inherited,
        "input_tokens": counted["input"][0] or 0,
        "output_tokens": counted["output"][0] or 0,
        "complete": cost.get("complete") is True and all(whole for _, whole in counted.values()),
    }


def total_text(total: dict | None) -> str:
    if not total:
        return ""
    used = total["input_tokens"] + total["output_tokens"]
    parts = [money(total["spent_usd"]) if total["spent_usd"] is not None else "$?"]
    if used:
        parts.append(f"{tokens(used)} tokens")
    return " · ".join(parts) + ("" if total["complete"] else "+")


def session_total(totals: list[dict | None]) -> dict | None:
    """Several chats' run totals added together: each run keeps its own ledger, so their sums do
    not overlap, except what a run restored from a saved step carries over, which is left out."""
    shown = [total for total in totals if total]
    if not shown:
        return None
    spent = [total["spent_usd"] for total in shown]
    return {
        "spent_usd": None
        if any(value is None for value in spent)
        else sum(value - total["inherited_usd"] for value, total in zip(spent, shown, strict=True)),
        "inherited_usd": 0,
        "input_tokens": sum(total["input_tokens"] for total in shown),
        "output_tokens": sum(total["output_tokens"] for total in shown),
        "complete": all(total["complete"] for total in shown) and len(shown) == len(totals),
    }
