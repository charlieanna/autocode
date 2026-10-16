"""Each escalation a run made, what its escalated attempts cost and how it ended (#612).

  rows(state)   the status view's `escalations` field: one row per escalation, oldest first
  lines(rows)   the lines `autocode --status` prints for a person on stderr, after the cost line

Two escalations are recorded today, and this module only reads them:

- an effort climb: autocode_escalation.advance moved a role one rung up its effort ladder
  (`reasoning_escalations`: the role, the trigger, the route before and after, when);
- the Builder's stronger attempt: its retry policy moved the Builder to the strong route once its
  ordinary retries were spent (`builder_retry_decisions` with action `escalate`). A parallel Builder
  that left that attempt to the parent run (action `defer`, adopted by the parent) is marked on the
  parent's row; the attempt is listed once the parent makes it.

An escalation's attempts are its role's stage records launched on the route it climbed to (dry runs
and the runner's own stages left out), from the escalation until the first of: a validation after it
that passed (that validation's attempt included), or the role escalating again the same way (its next
climb, or the Builder's next stronger attempt); a running attempt counts. Each is priced as the
view's `cost` prices a stage (autocode_usage.stage_rows at the dated table of autocode_prices):
reported, else estimated, else unpriced, never zero.

How it ended (`outcome`), from the first of these that applies (`outcome_basis` says which):

- the first decisive record after it: a validation that passed (`passed`, basis `validation`) or a
  later escalation of the same lane, the role's next climb or another stronger attempt for the same
  milestone (`failed_again`, basis `later_escalation`);
- the outcome the runner stamped on it (autocode_route_ladder.record_outcome): `passed` at a
  validation PASS, `paused` at the Builder retry-limit pause (basis `recorded`);
- the latest validation after its first attempt failed or was blocked (`failed_again`, `validation`);
- the run's status now: complete is `passed`, running is `running`, anything else (a pause, a wait
  for a person, a stop) is `paused` (basis `run_status`).

Pure over saved state; imports only autocode_cost, autocode_prices, autocode_roles and autocode_usage.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_prices as prices
    from . import autocode_roles as roles
    from . import autocode_usage as usage
except ImportError:
    import autocode_cost as cost
    import autocode_prices as prices
    import autocode_roles as roles
    import autocode_usage as usage

# The run statuses read as finished and as still working; autocode_run_view.COMPLETE names the same
# finished statuses, but the view imports this module, so it cannot be imported back.
FINISHED = ("TASK_COMPLETE", "COMPLETE")
WORKING = ("RUNNING", "DISCOVERING")
# A validation verdict after an escalation, as that escalation's outcome.
VERDICTS = {"PASS": "passed", "FAIL": "failed_again", "BLOCKED": "failed_again"}
# Outcomes and triggers in words, for lines().
SAID = {"passed": "passed", "failed_again": "failed again", "paused": "the run paused", "running": "still running"}


def _list(value) -> list:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _when(value) -> dt.datetime | None:
    """A saved timestamp as an aware datetime, or None when it cannot be read."""
    try:
        moment = dt.datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _route(value) -> tuple[Any, Any]:
    value = _dict(value)
    return value.get("model"), value.get("reasoning_effort")


def _side(model, effort, profile=None) -> dict:
    return {"model": model, "reasoning_effort": effort, "profile": profile}


def _role(record) -> Any:
    return record.get("route_role") or record.get("role")


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _recorded(state: RunState) -> list[dict]:
    """Every recorded escalation as {kind, role, lane, at, when, trigger, detail, from, to, ...}, oldest first."""
    found = []
    for event in _list(state.get("reasoning_escalations")):
        if not isinstance(event.get("role"), str):
            continue
        before, after = _dict(event.get("previous")), _dict(event.get("selected"))
        found.append(
            {
                "kind": "effort_climb",
                "role": event["role"],
                # Another climb of the role ends this one: it failed again, and its rung is left.
                "lane": ("climb", event["role"]),
                "window": ("climb", event["role"]),
                "at": event.get("at"),
                "trigger": event.get("trigger"),
                "detail": event.get("detail"),
                "struggle_id": event.get("struggle_id"),
                "from": _side(before.get("model"), before.get("reasoning_effort"), before.get("profile")),
                "to": _side(after.get("model"), after.get("reasoning_effort"), after.get("profile")),
                "stamp": event.get("outcome"),
                "failed": None,
                "deferred": False,
            }
        )
    decisions = _list(state.get("builder_retry_decisions"))
    lanes = _dict(state.get("builder_retries"))
    for index, decision in enumerate(decisions):
        if decision.get("action") != "escalate":
            continue
        failure = _dict(decision.get("failure"))
        failed = _dict(failure.get("record"))
        before = _dict(failed.get("launch_route")) or _dict(
            _dict(lanes.get(decision.get("milestone_key"))).get("initial_route")
        )
        found.append(
            {
                "kind": "stronger_attempt",
                "role": "terra",
                # One stronger attempt per milestone; the next milestone's moves the Builder again.
                "lane": ("stronger", decision.get("milestone_key")),
                "window": ("stronger", "terra"),
                "at": decision.get("at"),
                "trigger": f"{decision.get('failure_class') or 'execution'}_failure",
                "detail": decision.get("reason"),
                "struggle_id": None,
                "from": _side(*_route(before)),
                "to": _side(decision.get("selected_model"), decision.get("selected_effort")),
                "stamp": decision.get("outcome"),
                "failed": failed,
                "task_id": _dict(failure.get("binding")).get("task_id") or failed.get("task_id"),
                # A parallel Builder left this attempt to the parent run, which made it serially.
                "deferred": any(
                    earlier.get("action") == "defer" and earlier.get("milestone_key") == decision.get("milestone_key")
                    for earlier in decisions[:index]
                ),
            }
        )
    for row in found:
        row["when"] = _when(row["at"])
    return sorted(found, key=lambda row: (row["when"] is None, row["when"] or dt.datetime.min.replace(tzinfo=dt.UTC)))


def _attempts(state: RunState) -> list[dict]:
    """Each launched attempt with its price: the saved stage records (autocode_usage.stage_rows at the dated
    table, the basis of the view's `cost`), then the running stage, whose cost is not known yet."""
    records = [record for record in _list(state.get("stages")) if not record.get("dry_run")]
    priced = usage.stage_rows({"stages": records}, estimator=prices.estimate)
    found = [
        {"record": record, "when": _when(record.get("started_at")), **price}
        for record, price in zip(records, priced, strict=True)
        if record.get("runner_owned") is not True
    ]
    active = state.get("active_stage")
    if isinstance(active, dict) and not active.get("dry_run"):
        found.append({"record": active, "when": _when(active.get("started_at")), "cost_usd": None, "basis": "running"})
    return [row for row in found if row["when"] is not None]


def _validations(state: RunState, attempts) -> list[tuple[dt.datetime, Any]]:
    """(when its attempt started, verdict) for each saved validation that names the attempt that produced it."""
    started = {row["record"].get("output"): row["when"] for row in attempts if row["record"].get("output")}
    saved = [state.get("validation"), *(row.get("validation") for row in _list(state.get("validation_archive")))]
    found = [
        (started[validation["output"]], validation.get("verdict"))
        for validation in saved
        if isinstance(validation, dict) and validation.get("output") in started
    ]
    return sorted(found, key=lambda row: row[0])


def _cost(attempts, known) -> dict:
    """What the attempts cost: known USD (reported plus estimated), each basis, and whether every one is priced."""
    reported = [row["cost_usd"] for row in attempts if row["basis"] == "reported"]
    estimated = [row["cost_usd"] for row in attempts if row["basis"] == "estimated"]
    unpriced = sum(row["basis"] == "unknown" for row in attempts)
    # No attempt launched cost nothing; launched attempts none of which is priced cost an unknown amount.
    nothing = bool(attempts) and not reported and not estimated
    return {
        "usd": None if not known or nothing else round(float(sum(reported) + sum(estimated)), 6),
        "reported_usd": round(float(sum(reported)), 6) if reported else None,
        "estimated_usd": round(float(sum(estimated)), 6) if estimated else None,
        "unpriced_attempts": unpriced,
        "complete": bool(known)
        and unpriced == 0
        and not any(row.get("partial") or row["basis"] == "running" for row in attempts),
    }


def _outcome(state: RunState, escalation, later, first, validations) -> tuple[str, str, dt.datetime | None, bool]:
    """(outcome, basis, end, inclusive): how the escalation ended and where its attempts stop."""
    passed = next((when for when, verdict in validations if first and when >= first and verdict == "PASS"), None)
    if passed is not None and (later is None or passed < later):
        return "passed", "validation", passed, True
    if later is not None:
        return "failed_again", "later_escalation", later, False
    if escalation["stamp"] in ("passed", "paused"):
        return escalation["stamp"], "recorded", None, False
    verdicts = [verdict for when, verdict in validations if first and when >= first and verdict in VERDICTS]
    if verdicts:
        return VERDICTS[verdicts[-1]], "validation", None, False
    status = state.get("status") or ""
    return ("passed" if status in FINISHED else "running" if status in WORKING else "paused"), "run_status", None, False


def _next(escalations, escalation, key) -> dt.datetime | None:
    """When the next escalation after this one in the same lane or window was made, or None."""
    when = escalation["when"]
    return next(
        (
            other["when"]
            for other in escalations
            if other[key] == escalation[key] and when and other["when"] and other["when"] > when
        ),
        None,
    )


def rows(state: RunState) -> list[dict]:
    """The status view's `escalations`: each recorded escalation, oldest first, with the role and stage it
    was for, the route it climbed from and to, its attempts and their cost, and how it ended. Never raises on
    a damaged state; an escalation whose time cannot be read lists no attempts and an unknown cost."""
    escalations = _recorded(state)
    attempts = _attempts(state)
    validations = _validations(state, attempts)
    found = []
    for escalation in escalations:
        when, role = escalation["when"], escalation["role"]
        later = _next(escalations, escalation, "lane")
        closed = _next(escalations, escalation, "window")
        own = [row for row in attempts if _role(row["record"]) == role]
        launched = [
            row
            for row in own
            if when is not None
            and row["when"] >= when
            and _route(row["record"].get("launch_route")) == _route(escalation["to"])
        ]
        first = min((row["when"] for row in launched), default=None)
        outcome, basis, end, inclusive = _outcome(state, escalation, later, first, validations)
        launched = [
            row
            for row in launched
            if (closed is None or row["when"] < closed)
            and (end is None or (row["when"] <= end if inclusive else row["when"] < end))
        ]
        before = [row for row in own if when is not None and row["when"] < when]
        struggled = escalation["failed"] or (max(before, key=lambda row: row["when"])["record"] if before else {})
        stage = _text(struggled.get("stage")) or (_text(launched[0]["record"].get("stage")) if launched else None)
        found.append(
            {
                "kind": escalation["kind"],
                "role": role,
                "stage": stage,
                "job": roles.screen_name(stage, state) if stage else None,
                "at": _text(escalation["at"]),
                "trigger": _text(escalation["trigger"]),
                "detail": _text(escalation["detail"]),
                "struggle_id": _text(escalation["struggle_id"]),
                "task_id": _text(escalation.get("task_id") or struggled.get("task_id")),
                "from": escalation["from"],
                "to": escalation["to"],
                "deferred_by_parallel_builder": escalation["deferred"],
                "attempts": [
                    {
                        "stage": row["record"].get("stage"),
                        "iteration": row["record"].get("iteration"),
                        "started_at": row["record"].get("started_at"),
                        "active": row["basis"] == "running",
                        "cost_usd": row["cost_usd"],
                        "basis": row["basis"],
                    }
                    for row in launched
                ],
                "cost": _cost(launched, when is not None),
                "outcome": outcome,
                "outcome_basis": basis,
            }
        )
    return found


def _named(side) -> str:
    side = _dict(side)
    if side.get("profile"):
        return str(side["profile"])
    model, effort = side.get("model") or "an unknown model", side.get("reasoning_effort")
    return f"{model} at {effort}" if effort else str(model)


def lines(escalations) -> list[str]:
    """What `--status` prints for a person about the run's escalations: a count by outcome, then one line
    each (the job, the routes, the trigger, the attempts, their cost, the outcome). Empty when there are none."""
    escalations = [row for row in escalations if isinstance(row, dict)] if isinstance(escalations, list) else []
    if not escalations:
        return []
    counts = {key: sum(row.get("outcome") == key for row in escalations) for key in SAID}
    parts = ", ".join(f"{count} {SAID[key]}" for key, count in counts.items() if count)
    text = [f"Escalations: {len(escalations)} ({parts})."]
    for row in escalations:
        spend = _dict(row.get("cost"))
        tries = len(row.get("attempts") or [])
        if not tries:
            paid = "not launched"
        elif spend.get("usd") is None:
            paid = f"{tries} attempt{'s' if tries != 1 else ''}, cost unknown"
        else:
            floor = "" if spend.get("complete") else "at least "
            paid = f"{tries} attempt{'s' if tries != 1 else ''}, {floor}{cost.usd(spend['usd'])}"
        trigger = str(row.get("trigger") or "an escalation").replace("_", " ")
        text.append(
            f"  {row.get('job') or row.get('role')}: {_named(row.get('from'))} → {_named(row.get('to'))} after "
            f"{trigger}; {paid}; {SAID.get(row.get('outcome'), row.get('outcome'))}."
        )
    return text
