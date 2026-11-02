"""An attempt the run can price is checked against the person's cost cap before it launches, and a climbed
attempt against the time budgets too (#611, #2019).

The serial build loop calls guard() before every model launch. Any attempt of a job that already ran in this
run is projected from the job's last attempt, its recorded tokens at the saved route's dated price
(autocode_cost.crossing); past the cap, the run pauses PAUSED_COST_LIMIT before the attempt instead of after
it, so a run never launches a stage it can price past its cap (#2019). A job's first attempt has no projection.

autocode_escalation climbs a struggling role one rung on its effort ladder and records the climb in
`reasoning_escalations`; the role's next request is the first attempt on the climbed rung. While that first
attempt has not launched, guard() projects it from the role's last attempt and checks it against the run's
budgets (within_budgets):

- the cost cap (`--max-cost-usd`): the last attempt's recorded tokens at the climbed model's dated price
  (autocode_cost.crossing). Past the cap, the run pauses PAUSED_COST_LIMIT.
- the active-time budget (`--max-seconds`, when you set it): the last attempt's recorded duration, never more
  than the stage time limit that would stop it (time_crossing). Past the budget, the run pauses PAUSED_TIME_LIMIT.
- for a Builder attempt only, the milestone budget (`--max-milestone-seconds`, when you set it): the same
  projection added to the current milestone's counted time (milestone_crossing). Past the budget, the run pauses
  PAUSED_MILESTONE_BUDGET. That budget gates writer launches only (autocode_milestones.dispatch_guard), so a
  Tester's or Completion Reviewer's climb is not checked against it.

Each stop names the rung and the arithmetic, instead of climbing and stopping at the budget afterwards. The
climb stays saved, so resuming with a cap or budget of at least the one the stop names lets that attempt run.
The Builder retry policy's stronger attempt has the same checks (autocode_builder_policy.guard calls
within_budgets). When more than one would be crossed, the cost stop comes first, then the run's active time.

No cap or budget, no earlier attempt on the role, a model without a rate or an attempt without token counts or
a duration never pause here, as an unpriced stage never trips the cap. A runner-default or delegated active-time
budget is not projected: the stage-boundary check and AutoResolver's one-time extension govern it, as before.
A runner-default or delegated milestone budget is not projected either: the writer-launch check
(autocode_milestones.check_budget) governs it, as before. Nor is a progressive run's slice budget, which has its
own check (autocode_progressive_state.check_local_budget).
Once the climbed attempt has launched, the stage-boundary checks govern it. A parallel Builder sees only its own
spend and time, so it leaves the checks to the serial run.

Pure over saved state but for the pause it raises; imports only autocode_cost, autocode_milestone_scope,
autocode_roles and autocode_util.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_milestone_scope as milestone_scope
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_cost as cost
    import autocode_milestone_scope as milestone_scope
    import autocode_roles as roles
    import autocode_util as util


def _route(value):
    """(model, reasoning effort) of a saved route, launch route or escalation event side."""
    value = value if isinstance(value, dict) else {}
    return value.get("model"), value.get("reasoning_effort")


def _when(value):
    """A saved timestamp (an event's `at`, an attempt's `started_at`) as an aware datetime, or None."""
    try:
        moment = dt.datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _seconds(value):
    """A saved count of seconds, or None when it is not a finite nonnegative number."""
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def budget_seconds(settings):
    """The run's active-time budget in seconds when you set it (`--max-seconds N`), else None.

    None for no budget (absent or 0), and for the runner's default or a budget delegated to AutoResolver
    (`--autoresolver-managed-limits`): AutoResolver may extend those once at the stage boundary, so a
    projection must not pause the run for you before that boundary is reached.
    """
    settings = settings if isinstance(settings, dict) else {}
    limits = settings.get("limits") if isinstance(settings.get("limits"), dict) else {}
    origins = settings.get("budget_origins") if isinstance(settings.get("budget_origins"), dict) else {}
    value = _seconds(limits.get("max_seconds"))
    return value if value and origins.get("max_seconds") == "user_explicit" else None


def projected_seconds(settings, record):
    """The active time one more attempt is projected to take, from `record`, a finished attempt of the same job:
    {"seconds", "held"}, its recorded duration, held to the stage time limit (`held` is that limit, or None)
    when it ran longer, since that limit stops an attempt. None when the record has no duration. A higher
    effort can run longer than the attempt it follows, so this is an estimate, not a bound."""
    duration = _seconds(record.get("duration_seconds")) if isinstance(record, dict) else None
    if duration is None:
        return None
    limits = settings.get("limits") if isinstance(settings, dict) else None
    stage_limit = _seconds(limits.get("stage_timeout_seconds")) if isinstance(limits, dict) else None
    if stage_limit and duration > stage_limit:
        return {"seconds": stage_limit, "held": stage_limit}
    return {"seconds": duration, "held": None}


def time_crossing(state: RunState, record):
    """The arithmetic when one more attempt, projected from `record` (projected_seconds), would take the run's
    active time past the budget you set: {"projected", "held", "used", "total", "budget"}, or None when there is
    no such budget, no projection, or the attempt fits within the budget."""
    settings = state.get("settings") if isinstance(state, dict) else None
    limit = budget_seconds(settings)
    projected = projected_seconds(settings, record) if limit is not None else None
    if projected is None:
        return None
    used = _seconds(state.get("active_seconds")) or 0
    total = used + projected["seconds"]
    if total <= limit:
        return None
    return {"projected": projected["seconds"], "held": projected["held"], "used": used, "total": total, "budget": limit}


def _shown(seconds):
    """1,250 s; a fraction to one place (0.4 s), so a fake run's short attempts are not all shown as 0 s."""
    return f"{seconds:,.1f}".rstrip("0").rstrip(".") + " s"


# The stop reason when time_crossing() refuses a climbed attempt, beside autocode_cost.CLIMB_OVER_CAP.
CLIMB_OVER_TIME = (
    "{attempt} ({route}) did not launch: projected at {projected} of active time, {basis}'s duration{held}, it "
    "would take the run's active time from {used} to {total}, past the {budget} active-time budget you set. "
    "AutoCode never raises a budget you set; resume with --max-seconds {needed} or more (0 for no limit) to make "
    "this attempt"
)


def time_stop(over, *, attempt, basis, model, effort=None):
    """The stop reason for a climbed attempt that `over` (time_crossing) refused, worded like
    autocode_cost.climb_stop: the attempt, its route, the projection and its basis, the active time so far, the
    total, the budget and the budget to resume with, rounded up to a whole second so that it admits the attempt."""
    return CLIMB_OVER_TIME.format(
        attempt=attempt,
        route=f"{model} at {effort} effort" if effort else model,
        basis=basis,
        held=f", held to the {_shown(over['held'])} stage time limit" if over.get("held") else "",
        **{key: _shown(over[key]) for key in ("projected", "used", "total", "budget")},
        needed=math.ceil(over["total"]),
    )


def milestone_budget_seconds(settings):
    """The milestone active-time budget in seconds when you set it (`--max-milestone-seconds N`) with milestone
    checkpoints on, else None.

    None for checkpoints off, no budget (absent or 0), and the runner's default or a budget delegated to
    AutoResolver (`--autoresolver-managed-limits`): those are left to the writer-launch check
    (autocode_milestones.check_budget), which pauses once the milestone's time reaches them, as before.
    """
    settings = settings if isinstance(settings, dict) else {}
    checkpoints = (
        settings.get("milestone_checkpoints") if isinstance(settings.get("milestone_checkpoints"), dict) else {}
    )
    origins = settings.get("budget_origins") if isinstance(settings.get("budget_origins"), dict) else {}
    value = _seconds(checkpoints.get("max_seconds"))
    if checkpoints.get("enabled") is not True or origins.get("milestone_max_seconds") != "user_explicit":
        return None
    return value or None


def milestone_crossing(state: RunState, record):
    """The arithmetic when one more Builder attempt, projected from `record` (projected_seconds), would take the
    current milestone's counted active time past the milestone budget you set: {"projected", "held", "used",
    "total", "budget"}, or None.

    None when there is no such budget, when a progressive run's slice budget governs instead (the condition under
    which `--resume-paused --max-milestone-seconds` does not restart the count), when the current task is not on
    the approved contract or its milestone has no saved row yet (as autocode_milestones.progress reads it; the row
    is never created here), with no projection, and when the attempt fits within the budget.
    """
    settings = state.get("settings") if isinstance(state, dict) else None
    limit = milestone_budget_seconds(settings)
    if limit is None:
        return None
    progressive = state.get("progressive")
    if isinstance(progressive, dict) and progressive.get("budget"):
        return None
    task = state.get("current_task") or {}
    if not task or task.get("contract_hash") != (state.get("goal_contract") or {}).get("hash"):
        return None
    row = (state.get("milestone_progress") or {}).get(milestone_scope.key(state))
    projected = projected_seconds(settings, record)
    if not isinstance(row, dict) or projected is None:
        return None
    used = _seconds(row.get("seconds")) or 0
    total = used + projected["seconds"]
    if total <= limit:
        return None
    return {"projected": projected["seconds"], "held": projected["held"], "used": used, "total": total, "budget": limit}


# The stop reason when milestone_crossing() refuses a climbed Builder attempt, beside CLIMB_OVER_TIME.
CLIMB_OVER_MILESTONE = (
    "{attempt} ({route}) did not launch: projected at {projected} of active time, {basis}'s duration{held}, it "
    "would take this milestone's active time from {used} to {total}, past the {budget} milestone budget you set. "
    "AutoCode never raises a budget you set; resume with --max-milestone-seconds {needed} or more (0 for no limit), "
    "which also restarts this milestone's count, to make this attempt"
)


def milestone_stop(over, *, attempt, basis, model, effort=None):
    """The stop reason for a climbed Builder attempt that `over` (milestone_crossing) refused, worded like
    time_stop, with the total rounded up to a whole second as the budget to resume with.

    That total is always above the budget you set, so resuming with it never lowers that budget and is a change
    of it: reasserting the saved milestone budget alone does not acknowledge PAUSED_MILESTONE_BUDGET. It admits
    the attempt even before `--resume-paused --max-milestone-seconds M` restarts the milestone's count
    (autocode_action_recovery), after which the attempt is checked against M from 0 s."""
    return CLIMB_OVER_MILESTONE.format(
        attempt=attempt,
        route=f"{model} at {effort} effort" if effort else model,
        basis=basis,
        held=f", held to the {_shown(over['held'])} stage time limit" if over.get("held") else "",
        **{key: _shown(over[key]) for key in ("projected", "used", "total", "budget")},
        needed=math.ceil(over["total"]),
    )


def within_budgets(state: RunState, last, *, attempt, basis, model, effort=None, writer=False) -> None:
    """Pause before a climbed attempt on `model` launches when its projection from `last`, the job's last
    finished attempt, would take the spend past the cost cap (PAUSED_COST_LIMIT), the active time past the
    budget you set (PAUSED_TIME_LIMIT) or, for a `writer` (Builder) attempt, the milestone's active time past the
    milestone budget you set (PAUSED_MILESTONE_BUDGET). `attempt` names the climbed attempt and `basis` the attempt
    it is projected from, as in the stop reason."""
    over = cost.crossing(state, model, last)
    if over:
        raise util.Paused(
            "PAUSED_COST_LIMIT", cost.climb_stop(over, attempt=attempt, basis=basis, model=model, effort=effort)
        )
    late = time_crossing(state, last)
    if late:
        raise util.Paused(
            "PAUSED_TIME_LIMIT", time_stop(late, attempt=attempt, basis=basis, model=model, effort=effort)
        )
    spent = milestone_crossing(state, last) if writer else None
    if spent:
        raise util.Paused(
            "PAUSED_MILESTONE_BUDGET", milestone_stop(spent, attempt=attempt, basis=basis, model=model, effort=effort)
        )


def pending(state: RunState, role):
    """The climb of `role` whose first attempt has not launched yet, with the attempt to project it from:
    (escalation event, the role's last attempt), or None.

    A climb is pending while the role's saved route is still the rung its last recorded climb selected and no
    attempt of the role has run on that rung since the climb was recorded. A rung the route returns to later,
    such as the Builder retry policy restoring a milestone's checkers, is not a climb again once its attempt
    ran. An attempt on the rung whose start time cannot be read counts as run, and a climb whose time cannot be
    read is not priced, so a guess never pauses the run. Report repairs and dry runs are not attempts. A route
    the person set after the climb is theirs: it no longer matches the climb, so it is not priced here.
    """
    climbs = [
        event
        for event in state.get("reasoning_escalations") or ()
        if isinstance(event, dict) and event.get("role") == role
    ]
    route = _saved_route(state, role)
    if not climbs or route != _route(climbs[-1].get("selected")):
        return None
    climbed_at = _when(climbs[-1].get("at"))
    attempts = attempts_of(state, role)
    if climbed_at is None or not attempts:
        return None
    for row in attempts:
        started = _when(row.get("started_at"))
        if _route(row["launch_route"]) == route and (started is None or started >= climbed_at):
            return None
    return climbs[-1], attempts[-1]


def _attempts(state: RunState):
    """The finished attempts of this run, oldest first: the stage rows that recorded a launch route. Report repairs
    and dry runs are not attempts."""
    return [
        row
        for row in state.get("stages") or ()
        if isinstance(row, dict)
        and isinstance(row.get("launch_route"), dict)
        and not row.get("report_only")
        and not row.get("dry_run")
    ]


def attempts_of(state: RunState, role):
    """`role`'s finished attempts in this run, oldest first: the attempts on its route."""
    return [row for row in _attempts(state) if (row.get("route_role") or row.get("role")) == role]


def job_attempts(state: RunState, stage):
    """The finished attempts of `stage`'s job in this run, oldest first: the attempts of every stage that does the
    same job on screen (autocode_roles.screen_name), whatever route each ran on. Under reviewer routing the Tester,
    for one, runs `sol` on one route and `astra_checkpoint` on another; the second is the job's next attempt."""
    job = roles.screen_name(stage, state)
    return [row for row in _attempts(state) if roles.screen_name(row.get("stage"), state) == job]


def _saved_route(state: RunState, role):
    """(model, reasoning effort) of `role`'s saved route."""
    return _route(((state.get("settings") or {}).get("roles") or {}).get(role))


def guard(state: RunState, stage, role) -> None:
    """Pause before `stage` launches on `role`'s route when the attempt is projected past the cost cap, naming the
    job, the route and the arithmetic.

    A climbed attempt (pending) is checked against the cost cap, the active-time budget and, for the Builder, the
    milestone budget (within_budgets). Any other attempt of a job that already ran in this run is priced against
    the cost cap alone (#2019): the job's last attempt's tokens (job_attempts: the same job on screen, on whatever
    route it ran) at the saved route's rate, through the same arithmetic and stop as a climb, so a run never
    launches a stage it can price past its cap. A job's first attempt has no projection and launches; the
    stage-boundary check governs it."""
    if state.get("parent_run"):
        return
    climb = pending(state, role)
    if climb is not None:
        event, last = climb
        model, effort = _saved_route(state, role)
        rung = (event.get("selected") or {}).get("profile") or "its next rung"
        within_budgets(
            state,
            last,
            attempt=f"The {roles.screen_name(stage, state)}'s climb to {rung}",
            basis=f"the last {roles.screen_name(last.get('stage'), state)} attempt",
            model=model,
            effort=effort,
            writer=stage == "terra",
        )
        return
    attempts = job_attempts(state, stage)
    if not attempts:
        return
    model, effort = _saved_route(state, role)
    over = cost.crossing(state, model, attempts[-1])
    if over:
        raise util.Paused(
            "PAUSED_COST_LIMIT",
            cost.climb_stop(
                over,
                attempt=f"The next {roles.screen_name(stage, state)} attempt",
                basis=f"the last {roles.screen_name(attempts[-1].get('stage'), state)} attempt",
                model=model,
                effort=effort,
            ),
        )
