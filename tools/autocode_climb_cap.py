"""A role's climb on its effort ladder is priced against the person's cost cap before its attempt launches (#611).

autocode_escalation climbs a struggling role one rung on its effort ladder and records the climb in
`reasoning_escalations`; the role's next request is the first attempt on the climbed rung. The serial build
loop calls guard() before every model launch. While that first attempt has not launched, guard() projects it
from the role's last attempt: its recorded tokens at the climbed model's dated price (autocode_cost.crossing).
When the spend so far plus that projection would pass `--max-cost-usd`, the run pauses PAUSED_COST_LIMIT
before the attempt starts and names the rung and the arithmetic, instead of climbing and stopping at the cap
afterwards. The climb stays saved, so resuming with a cap of at least the one the stop names lets that attempt
run. The Builder retry policy's stronger attempt has the same check (autocode_builder_policy.guard).

No cap, no earlier attempt on the role, a model without a rate or an attempt without token counts never pause
here, as an unpriced stage never trips the cap. Once the climbed attempt has launched, the stage-boundary
check governs the spend. A parallel Builder sees only its own spend, so it leaves the check to the serial run.

Pure over saved state but for the pause it raises; imports only autocode_cost, autocode_roles and autocode_util.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_cost as cost
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_cost as cost
    import autocode_roles as roles
    import autocode_util as util


def _route(value):
    """(model, reasoning effort) of a saved route, launch route or escalation event side."""
    value = value if isinstance(value, dict) else {}
    return value.get("model"), value.get("reasoning_effort")


def pending(state: RunState, role):
    """The climb of `role` whose first attempt has not launched yet, with the attempt to project it from:
    (escalation event, the role's last attempt), or None.

    A climb is pending while the role's saved route is still the rung its last recorded climb selected and the
    role's last attempt ran on another route. Report repairs and dry runs are not attempts. A route the person
    set after the climb is theirs: it no longer matches the climb, so it is not priced here.
    """
    climbs = [
        event
        for event in state.get("reasoning_escalations") or ()
        if isinstance(event, dict) and event.get("role") == role
    ]
    route = _route(((state.get("settings") or {}).get("roles") or {}).get(role))
    if not climbs or route != _route(climbs[-1].get("selected")):
        return None
    attempts = [
        row
        for row in state.get("stages") or ()
        if isinstance(row, dict)
        and (row.get("route_role") or row.get("role")) == role
        and isinstance(row.get("launch_route"), dict)
        and not row.get("report_only")
        and not row.get("dry_run")
    ]
    if not attempts or _route(attempts[-1]["launch_route"]) == route:
        return None
    return climbs[-1], attempts[-1]


def guard(state: RunState, stage, role) -> None:
    """Pause PAUSED_COST_LIMIT before `stage` launches on `role`'s climbed rung when the attempt's projected cost
    would take the spend past the cap, naming the job, the rung and the arithmetic."""
    if state.get("parent_run"):
        return
    climb = pending(state, role)
    if climb is None:
        return
    event, last = climb
    model, effort = _route(state["settings"]["roles"][role])
    over = cost.crossing(state, model, last)
    if over:
        rung = (event.get("selected") or {}).get("profile") or "its next rung"
        raise util.Paused(
            "PAUSED_COST_LIMIT",
            cost.climb_stop(
                over,
                attempt=f"The {roles.screen_name(stage, state)}'s climb to {rung}",
                basis=f"the last {roles.screen_name(last.get('stage'), state)} attempt",
                model=model,
                effort=effort,
            ),
        )
