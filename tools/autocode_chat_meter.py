"""What each stage of a run did and what it used, for the full-screen chat: a display of the
public status view's own attempt and cost records, never a route choice or a spend decision.

Every figure shown is a value the core wrote: an attempt's route, start, duration, outcome and
attributed tokens (``usage.accounting.attempts``), a stage's accounted spend
(``cost.breakdown.by_stage``), the run's accounted total (``cost.spent_usd``) and the tries the
core still allows a failing stage (``progress.retry``). The only arithmetic is adding attempt
tokens and durations together (one stage's for its row, every stage's for the run's working time),
and adding separate chats' run totals for the session line; those are distinct ledgers, so nothing
is counted twice, and a total built from an incomplete part says so.
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
        ("timed_out", "timed out"),
        ("interrupted", "interrupted"),
        ("rejected", "answer rejected"),
    ):
        if row.get(key):
            return words
    exit_code = row.get("exit_code")
    return f"exit {exit_code}" if type(exit_code) is int and exit_code else ""


def _route(row: dict) -> str:
    route = {**_mapping(row.get("launch_route"))}
    model = route.get("model") or row.get("model")
    # The launch route keeps the saved level; the row keeps the level the stage ran at (#2747).
    effort = row.get("reasoning_effort") or route.get("reasoning_effort")
    return " ".join(str(part) for part in (model, effort) if isinstance(part, str) and part.strip())


def stages(view: dict | None, now: datetime | None = None) -> list[dict]:
    """One row per stage the run has launched, in the order it first ran.

    Each row: ``job`` (the screen name), ``route`` (model and reasoning level of its newest
    attempt), ``state`` (working, done, failed or retrying), ``attempts``, ``seconds``
    (the stage total), ``attempt_seconds`` (the newest attempt),
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
        # Only an attempt right after one that did not succeed is a retry. Once a failed attempt was
        # repaired, the stage's next run (a further conversation turn, a second review round) is new work.
        retry = newest.get("active") and len(rows) > 1 and _failed(rows[-2])
        if newest.get("active"):
            state = RETRYING if retry else WORKING
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
        previous = _why(rows[-2]) if retry else ""
        result.append(
            {
                "stage": stage,
                "job": str(newest.get("job") or stage),
                "route": _route(newest),
                "state": state,
                "attempts": len(rows),
                "failed_attempts": sum(_failed(row) for row in rows),
                "seconds": sum(seconds) if seconds else None,
                "attempt_seconds": _seconds(newest, now),
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
        head.append(duration(row["seconds"]) + (" total" if row["attempts"] > 1 else ""))
    if row["spent_usd"] is not None and (row["spent_usd"] or row["route"]):  # a stage with no model costs nothing
        head.append(money(row["spent_usd"]))
    if row["attempts"] > 1:
        # A stage that failed and is tried again is on an attempt; one that ran again after succeeding
        # (a second review round) simply ran more than once.
        retried = row["state"] in (RETRYING, FAILED) and row["failed_attempts"]
        head.append(f"attempt {row['attempts']}" if retried else f"{row['attempts']} runs")
    detail = [row["route"]] if row["route"] else []
    used = row["input_tokens"] + row["output_tokens"]
    if used:
        detail.append(f"{tokens(used)}{'' if row['tokens_complete'] else '+'} tokens")
    elif row["route"] and not row["tokens_complete"]:
        detail.append("tokens pending")
    if row["why"] and row["state"] != DONE:
        detail.append(row["why"])
    style = {DONE: "good", WORKING: "accent", FAILED: "bad", RETRYING: "warn"}[row["state"]]
    return f"{mark} {' · '.join(head)}", "  " + " · ".join(detail), style


def tries_left(view: dict | None) -> str:
    """How many more failures the core allows the current stage before it stops trying, from the
    view's own ``progress.retry`` (``failures`` so far, ``limit``): "2 tries left", "last try", or
    empty when the view does not say."""
    retry = _mapping(_mapping(_mapping(view).get("progress")).get("retry"))
    failed, limit = retry.get("failures"), retry.get("limit")
    if type(failed) is not int or type(limit) is not int or limit <= 0 or failed < 0:
        return ""
    left = limit - failed
    if left <= 0:
        return "no tries left"
    return "last try" if left == 1 else f"{left} tries left"


def struggling(view: dict | None, now: datetime | None = None) -> str:
    """For the working stage, when it is trying again after an attempt that did not succeed: which
    attempt, how many tries the core still allows, how long it has run and why the previous one
    failed. Empty when nothing is wrong, as for a stage that runs again after succeeding (a second
    review round)."""
    working = [row for row in stages(view, now) if row["state"] in (WORKING, RETRYING)]
    if not working or working[-1]["state"] != RETRYING:
        return ""
    row = working[-1]
    parts = [row["job"], f"attempt {row['attempts']}"]
    if left := tries_left(view):
        parts.append(left)
    if row["attempt_seconds"] is not None:
        parts.append(duration(row["attempt_seconds"]))
    if row["why"]:
        parts.append(f"last one {row['why']}")
    return " · ".join(parts)


def run_seconds(view: dict | None, now: datetime | None = None) -> float | None:
    """How long the run's stages have worked in all: every recorded attempt's own duration added
    together, the working one measured to ``now``. None before any attempt has a duration. Time
    waiting for a person between attempts is not in it."""
    rows = _rows(_mapping(_mapping(_mapping(view).get("usage")).get("accounting")).get("attempts"))
    seconds = [value for row in rows if (value := _seconds(row, now)) is not None]
    return sum(seconds) if seconds else None


def worked_text(view: dict | None, now: datetime | None = None) -> str:
    """The run's working time as the footer and the Stages section show it: "worked 42m05s"."""
    seconds = run_seconds(view, now)
    return f"worked {duration(seconds)}" if seconds is not None else ""


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
        "cost_complete": cost.get("complete") is True,
        "tokens_complete": all(whole for _, whole in counted.values()),
    }


def total_text(total: dict | None, cap=None, *, show_pending: bool = False) -> str:
    """A total as "$0.42 · 184k tokens", "$1.73 of $5.00 · 843k tokens" with the run's spending cap.
    A "+" marks the figure that is a floor: "$0.42+" when some cost is unaccounted, "184k+ tokens"
    when some usage is unreported."""
    if not total:
        return ""
    used = total["input_tokens"] + total["output_tokens"]
    spent = money(total["spent_usd"]) if total["spent_usd"] is not None else "$?"
    parts = [spent + ("" if total["cost_complete"] or spent == "$?" else "+")]
    if _number(cap) and cap:
        parts[0] += f" of {money(cap)}"
    if used:
        parts.append(f"{tokens(used)}{'' if total['tokens_complete'] else '+'} tokens")
    elif show_pending and not total["tokens_complete"]:
        parts.append("tokens pending")
    return " · ".join(parts)


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
        # A chat not read yet leaves both figures a floor.
        "cost_complete": all(total["cost_complete"] for total in shown) and len(shown) == len(totals),
        "tokens_complete": all(total["tokens_complete"] for total in shown) and len(shown) == len(totals),
    }


class TaskUsage:
    """One task's public accounting snapshot, updated by its currently observed run.

    Repository switches change which row is updated, never which ledgers are
    included. An unrelated chat uses its own meter. Reopening reloads the core's
    complete run inventory, including earlier joint attempts.
    """

    def __init__(self):
        self.rows = {}
        self.anchor = None

    def accept(self, snapshot):
        self.anchor = snapshot["anchor"]
        self.rows = {row["run_dir"]: run_total(row) for row in snapshot["runs"]}

    def observe(self, view):
        run = view.get("run_dir")
        if self.anchor is not None and run == self.anchor:
            integration = (view.get("coordination") or {}).get("integration") or {}
            if integration.get("workspace") and integration.get("run_dir"):
                # A newly prepared joint run was absent from the earlier full
                # snapshot. Only this task's anchor can publish its membership.
                self.rows.setdefault(integration["run_dir"], None)
        if run in self.rows:
            self.rows[run] = run_total(view)

    def total(self, run):
        if run not in self.rows:
            return None
        totals = list(self.rows.values())
        result = session_total(totals)
        if result is None:
            result = {
                "spent_usd": None,
                "inherited_usd": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_complete": False,
                "tokens_complete": False,
            }
        # A missing price leaves a known lower bound, not a zero-cost run and
        # not a reason to hide other runs' already recorded spend.
        known = [row for row in totals if row is not None and row["spent_usd"] is not None]
        result["spent_usd"] = sum(row["spent_usd"] - row["inherited_usd"] for row in known) if known else None
        result["cost_complete"] = result["cost_complete"] and len(known) == len(totals)
        return result
