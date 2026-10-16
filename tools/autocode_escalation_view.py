"""Each escalation a run made, what its escalated attempts cost and how it ended (#612).

  rows(state)   the status view's `escalations` field: one row per escalation, oldest first
  lines(rows)   the lines `autocode --status` prints for a person on stderr, after the cost line

Two escalations are recorded today, and this module only reads them:

- an effort climb: autocode_escalation.advance moved a role one rung up its effort ladder
  (`reasoning_escalations`: the role, the trigger, the route before and after, when);
- the Builder's stronger attempt: its retry policy moved the Builder to the strong route once its
  ordinary retries were spent (`builder_retry_decisions` with action `escalate`; its `failure` is the
  output path of the attempt that failed, whose stage record names the task). A parallel Builder
  that left that attempt to the parent run (action `defer`, adopted by the parent) is marked on the
  parent's row; the attempt is listed once the parent makes it.

An escalation's attempts are its role's stage records launched on the route it climbed to (dry runs
and the runner's own stages left out), from the escalation until the first of: a validation of its
work that passed (that attempt included), or the role's next escalation of either kind (its next
climb, the Builder's stronger attempt); a running attempt counts. A validation judged its work when
its own attempt is one of them, or, for a role that does not validate (the Builder), when the role's
last attempt before it is one of them. Each is priced as the view's `cost` prices a stage
(autocode_usage.stage_rows at the dated table of autocode_prices): reported, else estimated, else
unpriced, never zero. An escalation made before the run was created was made in the run a checkpoint
continuation restored (`inherited`): its attempts there are not in this run's records, so its cost
is unknown, never zero.

How it ended (`outcome`), from the first of these that applies (`outcome_basis` says which):

- the first decisive record after it: a validation of its work that passed (`passed`, basis
  `validation`) or a later escalation of the same lane (the role's next climb, another stronger
  attempt for the same milestone) or of the other kind made from one of its attempts (the Builder's
  stronger attempt after its climb, or its climb after the stronger attempt) (`failed_again`, basis
  `later_escalation`);
- the outcome the runner stamped on it (autocode_route_ladder.record_outcome): `passed` at a
  validation PASS, `paused` at the Builder retry-limit pause (basis `recorded`);
- the latest validation of its work failed or was blocked (`failed_again`, `validation`);
- the run's status now: complete is `passed`, running is `running`, anything else (a pause, a wait
  for a person, a stop) is `paused` (basis `run_status`).

Pure over saved state; imports only autocode_cost, autocode_prices, autocode_roles and autocode_usage.
"""

from __future__ import annotations

import bisect
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


def _latest(rows, before) -> dict | None:
    """The row of `rows` that started last before `before`, or None."""
    earlier = [row for row in rows if before is not None and row["when"] < before]
    return max(earlier, key=lambda row: row["when"]) if earlier else None


def _by_role(attempts) -> dict:
    """Each role's attempts in the order they started, with their start times, for _before."""
    index: dict = {}
    for row in sorted(attempts, key=lambda row: row["when"]):
        role = _role(row["record"])
        rows, times = index.setdefault(role if isinstance(role, str) else None, ([], []))
        rows.append(row)
        times.append(row["when"])
    return index


def _before(index, role, when) -> dict | None:
    """The role's attempt that started last before `when`, or None."""
    rows, times = index.get(role if isinstance(role, str) else None, ([], []))
    at = bisect.bisect_left(times, when) if when is not None else 0
    return rows[at - 1] if at else None


def _job(stage, role, state: RunState | None = None) -> str | None:
    """The job as the screen names it (autocode_roles): the stage's, else the route role's. A route role is a stage
    code name (terra, sol) or a job key (completion, resolver)."""
    if isinstance(stage, str) and stage:
        return roles.screen_name(stage, state)
    if not isinstance(role, str) or not role:
        return None
    if roles.job_for_stage(role, state) is None and role in roles.SCREEN:
        return roles.SCREEN[role]
    return roles.screen_name(role, state)


def _builder_failure(decision, attempts) -> tuple[dict | None, str | None]:
    """(the Builder attempt that failed, its task) for a stronger-attempt decision, or None for what is not found.

    The decision's `failure` is the output path of the attempt that failed (autocode_builder_policy.failure's
    evidence): the Builder's own, or the Tester's or Completion Reviewer's that sent its work back. That record
    names the task; the Builder attempt is then the Builder's last for that task before the decision, so another
    milestone's attempts in a parallel batch are never taken for it."""
    failure = decision.get("failure")
    found = next(
        (row["record"] for row in attempts if isinstance(failure, str) and row["record"].get("output") == failure),
        None,
    )
    if found is None:
        return None, None
    task = _text(found.get("task_id"))
    if _role(found) == "terra":
        return found, task
    builders = [row for row in attempts if _role(row["record"]) == "terra" and row["record"].get("task_id") == task]
    latest = _latest(builders, _when(decision.get("at"))) if task else None
    return (latest["record"] if latest else None), task


def _recorded(state: RunState, attempts) -> list[dict]:
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
                "at": event.get("at"),
                "trigger": event.get("trigger"),
                "detail": event.get("detail"),
                "struggle_id": event.get("struggle_id"),
                "from": _side(before.get("model"), before.get("reasoning_effort"), before.get("profile")),
                "to": _side(after.get("model"), after.get("reasoning_effort"), after.get("profile")),
                "initial": None,
                "stamp": event.get("outcome"),
                "failed": None,
                "task_id": None,
                "deferred": False,
            }
        )
    decisions = _list(state.get("builder_retry_decisions"))
    lanes = _dict(state.get("builder_retries"))
    for index, decision in enumerate(decisions):
        if decision.get("action") != "escalate":
            continue
        failed, task = _builder_failure(decision, attempts)
        found.append(
            {
                "kind": "stronger_attempt",
                "role": "terra",
                # One stronger attempt per milestone; the next milestone's moves the Builder again.
                "lane": ("stronger", decision.get("milestone_key")),
                "at": decision.get("at"),
                "trigger": f"{decision.get('failure_class') or 'execution'}_failure",
                "detail": decision.get("reason"),
                "struggle_id": None,
                "from": None,
                "to": _side(decision.get("selected_model"), decision.get("selected_effort")),
                # The Builder's route when its milestone began, for a failed attempt that is not found.
                "initial": _dict(_dict(lanes.get(_text(decision.get("milestone_key")))).get("initial_route")),
                "stamp": decision.get("outcome"),
                "failed": failed,
                "task_id": task,
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


def _validations(state: RunState, attempts) -> list[tuple[dt.datetime, Any, dict]]:
    """(when its attempt started, verdict, that attempt) for each saved validation that names the attempt that
    produced it."""
    started = {row["record"]["output"]: row for row in attempts if isinstance(row["record"].get("output"), str)}
    saved = [state.get("validation"), *(row.get("validation") for row in _list(state.get("validation_archive")))]
    found = [
        (started[validation["output"]]["when"], validation.get("verdict"), started[validation["output"]])
        for validation in saved
        if isinstance(validation, dict)
        and isinstance(validation.get("output"), str)
        and validation["output"] in started
    ]
    return sorted(found, key=lambda row: row[0])


def _judged(escalation, validations, index) -> list[tuple[dt.datetime, Any]]:
    """(when, verdict) of each validation of the escalation's work: one an attempt of it produced, or, for a role
    that does not validate (the Builder), one run after the role's last attempt, when that is an attempt of it. A
    validation of another milestone, or of work on a route the role was moved to since, is not its work."""
    mine = {id(row) for row in escalation["mine"]}
    found = []
    for when, verdict, attempt in validations:
        if _role(attempt["record"]) != escalation["role"]:
            attempt = _before(index, escalation["role"], when)
        if attempt is not None and id(attempt) in mine:
            found.append((when, verdict))
    return found


def _cost(attempts, known, inherited) -> dict:
    """What the attempts cost: known USD (reported plus estimated), each basis, and whether every one is priced."""
    reported = [row["cost_usd"] for row in attempts if row["basis"] == "reported"]
    estimated = [row["cost_usd"] for row in attempts if row["basis"] == "estimated"]
    unpriced = sum(row["basis"] == "unknown" for row in attempts)
    # No attempt launched cost nothing; launched attempts none of which is priced cost an unknown amount, and so
    # does an escalation made before a restored checkpoint, whose attempts there are not in this run's records.
    nothing = (bool(attempts) or inherited) and not reported and not estimated
    return {
        "usd": None if not known or nothing else round(float(sum(reported) + sum(estimated)), 6),
        "reported_usd": round(float(sum(reported)), 6) if reported else None,
        "estimated_usd": round(float(sum(estimated)), 6) if estimated else None,
        "unpriced_attempts": unpriced,
        "complete": bool(known)
        and not inherited
        and unpriced == 0
        and not any(row.get("partial") or row["basis"] == "running" for row in attempts),
    }


def _outcome(state: RunState, escalation, later, judged) -> tuple[str, str, dt.datetime | None, bool]:
    """(outcome, basis, end, inclusive): how the escalation ended and where its attempts stop."""
    passed = next((when for when, verdict in judged if verdict == "PASS"), None)
    if passed is not None and (later is None or passed < later):
        return "passed", "validation", passed, True
    if later is not None:
        return "failed_again", "later_escalation", later, False
    if escalation["stamp"] in ("passed", "paused"):
        return escalation["stamp"], "recorded", None, False
    verdicts = [verdict for when, verdict in judged if verdict in VERDICTS]
    if verdicts:
        return VERDICTS[verdicts[-1]], "validation", None, False
    status = state.get("status") or ""
    return ("passed" if status in FINISHED else "running" if status in WORKING else "paused"), "run_status", None, False


def _next(escalations, escalation, ends) -> dt.datetime | None:
    """When the first escalation after this one for which `ends(escalation, other)` holds was made, or None."""
    when = escalation["when"]
    return next(
        (
            other["when"]
            for other in escalations
            if when and other["when"] and other["when"] > when and ends(escalation, other)
        ),
        None,
    )


def _same_role(escalation, other) -> bool:
    """The role's next escalation of either kind takes the attempts after it."""
    return other["role"] == escalation["role"]


def _ends(escalation, other) -> bool:
    """Whether `other`, made after `escalation`, ends it as failed again: the role escalated again the same way (its
    next climb, another stronger attempt for the milestone), or the other way from one of its attempts (the Builder's
    stronger attempt after its climb, or its climb after the stronger attempt). Another milestone's stronger attempt
    does not end it."""
    return _same_role(escalation, other) and (
        other["lane"] == escalation["lane"]
        or (other["kind"] != escalation["kind"] and id(other["struggled"]) in escalation["mine_records"])
    )


def rows(state: RunState) -> list[dict]:
    """The status view's `escalations`: each recorded escalation, oldest first, with the role and stage it was
    for, the route it climbed from and to, its attempts and their cost, and how it ended. Never raises on a
    damaged state; an escalation whose time cannot be read lists no attempts and an unknown cost."""
    attempts = _attempts(state)
    escalations = _recorded(state, attempts)
    validations = _validations(state, attempts)
    index = _by_role(attempts)
    created = _when(state.get("created_at"))
    for escalation in escalations:
        when, role = escalation["when"], escalation["role"]
        latest = _before(index, role, when)
        # The attempt it was made from: the failed Builder attempt the decision names, else the role's last.
        escalation["struggled"] = escalation["failed"] or (latest["record"] if latest else {})
        if escalation["kind"] == "stronger_attempt":
            route = _dict(escalation["struggled"].get("launch_route")) or escalation["initial"]
            escalation["from"] = _side(*_route(route))
        closed = _next(escalations, escalation, _same_role)
        escalation["mine"] = [
            row
            for row in index.get(role, ([], []))[0]
            if when is not None
            and row["when"] >= when
            and (closed is None or row["when"] < closed)
            and _route(row["record"].get("launch_route")) == _route(escalation["to"])
        ]
        escalation["mine_records"] = {id(row["record"]) for row in escalation["mine"]}
    found = []
    for escalation in escalations:
        when, role, mine = escalation["when"], escalation["role"], escalation["mine"]
        later = _next(escalations, escalation, _ends)
        judged = _judged(escalation, validations, index)
        outcome, basis, end, inclusive = _outcome(state, escalation, later, judged)
        launched = [row for row in mine if end is None or (row["when"] <= end if inclusive else row["when"] < end)]
        struggled = escalation["struggled"]
        stage = _text(struggled.get("stage")) or (_text(launched[0]["record"].get("stage")) if launched else None)
        # Made before this run was created: in the run a checkpoint continuation restored, whose stage records
        # (and the cost of its attempts there) this run does not hold; that spend is in the view's cost.
        inherited = when is not None and created is not None and when < created
        found.append(
            {
                "kind": escalation["kind"],
                "role": role,
                "stage": stage,
                "job": _job(stage, role, state),
                "at": _text(escalation["at"]),
                "trigger": _text(escalation["trigger"]),
                "detail": _text(escalation["detail"]),
                "struggle_id": _text(escalation["struggle_id"]),
                "task_id": _text(escalation["task_id"] or struggled.get("task_id")),
                "from": escalation["from"],
                "to": escalation["to"],
                "deferred_by_parallel_builder": escalation["deferred"],
                "inherited": inherited,
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
                "cost": _cost(launched, when is not None, inherited),
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
        attempts = f"{tries} attempt{'s' if tries != 1 else ''}" + (
            " since the restored checkpoint" if row.get("inherited") else ""
        )
        if not tries:
            paid = "made before the restored checkpoint, cost unknown" if row.get("inherited") else "not launched"
        elif spend.get("usd") is None:
            paid = f"{attempts}, cost unknown"
        else:
            floor = "" if spend.get("complete") else "at least "
            paid = f"{attempts}, {floor}{cost.usd(spend['usd'])}"
        trigger = str(row.get("trigger") or "an escalation").replace("_", " ")
        job = row.get("job") or _job(row.get("stage"), row.get("role")) or "A role"
        text.append(
            f"  {job}: {_named(row.get('from'))} → {_named(row.get('to'))} after "
            f"{trigger}; {paid}; {SAID.get(row.get('outcome'), row.get('outcome'))}."
        )
    return text
