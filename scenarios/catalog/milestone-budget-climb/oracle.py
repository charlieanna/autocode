"""The greeting repair (checks as in completion-rework-direct), finished after the Builder's stronger attempt was
held at the milestone budget the person set (#611, #1414).

In check mode (``run is None``) only the product is scored, so the controls reject the seed and the broken overlays
and accept the reference. With a run it also reads the run's saved state, as cost-cap-resume does, to check that:

- the run paused PAUSED_MILESTONE_BUDGET once, and that stop named the stronger attempt, its route and arithmetic
  that holds: the projection is the last Builder attempt's recorded duration, the milestone's time so far is within
  the scenario's budget and the total is past it, and the budget it advises resuming with covers that total;
- no Builder attempt ran on that route before the person acknowledged the stop, and exactly one ran after it;
- the person acknowledged it once, with a budget that covers the total, and the run then completed.
"""

import datetime as dt
import json
import re
import runpy
from pathlib import Path

from harness.oracle import Check

PAUSE = "PAUSED_MILESTONE_BUDGET"
BUDGET_FLAG = "--max-milestone-seconds"
# autocode_climb_cap.CLIMB_OVER_MILESTONE for the Builder retry policy's stronger attempt, as the person reads it.
STOP = re.compile(
    r"The Builder's stronger attempt \((?P<model>\S+) at (?P<effort>\S+) effort\) did not launch: projected at "
    r"(?P<projected>[\d,.]+) s of active time, the last Builder attempt's duration, it would take this milestone's "
    r"active time from (?P<used>[\d,.]+) s to (?P<total>[\d,.]+) s, past the (?P<budget>[\d,.]+) s milestone budget "
    r"you set\. AutoCode never raises a budget you set; resume with --max-milestone-seconds (?P<needed>\d+) or more"
)


def product_checks(project, scenario):
    sibling = runpy.run_path(str(Path(__file__).resolve().parents[1] / "completion-rework-direct" / "oracle.py"))
    return sibling["check"](project, scenario)


def _state(run) -> dict:
    path = Path(run["run_dir"]) / "state.json" if run.get("run_dir") else None
    return json.loads(path.read_text()) if path and path.is_file() else {}


def _flag(flags, name):
    """The number after ``name`` in a list of CLI flags, or None."""
    flags = list(flags)
    return float(flags[flags.index(name) + 1]) if name in flags[:-1] else None


def _when(text):
    try:
        return dt.datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _builders(state) -> list[dict]:
    """The Builder's attempts, oldest first; report repairs and dry runs are not attempts."""
    return [
        row
        for row in state.get("stages") or []
        if isinstance(row, dict)
        and row.get("stage") == "terra"
        and isinstance(row.get("launch_route"), dict)
        and not row.get("report_only")
        and not row.get("dry_run")
    ]


def _route(row) -> tuple:
    return row["launch_route"].get("model"), row["launch_route"].get("reasoning_effort")


def check(project, scenario, run=None):
    checks = product_checks(project, scenario)
    if run is None:
        return checks
    state = _state(run)
    escalations = ((state.get("resolver") or {}).get("human_escalations") or {}).values()
    proposals = [((row.get("identity") or {}).get("proposal") or {}) for row in escalations if isinstance(row, dict)]
    held = [row for row in proposals if (row.get("origin") or {}).get("pause_status") == PAUSE]
    checks.append(
        Check(
            "paused_at_the_milestone_budget_once",
            len(held) == 1,
            f"escalation origins: {[(row.get('origin') or {}).get('pause_status') for row in proposals]}",
        )
    )
    text = ((held[0].get("request") or {}).get("discovered") or "") if held else ""
    stop = STOP.search(text)
    checks.append(Check("stop_names_the_stronger_attempt", bool(stop), text[:400] or "no milestone-budget stop"))
    builders = _builders(state)
    climbed = (stop["model"], stop["effort"]) if stop else None
    stronger = [row for row in builders if _route(row) == climbed]
    earlier = [row for row in builders if _route(row) != climbed]
    if stop:
        projected, used, total, shown, needed = (
            float(stop[key].replace(",", "")) for key in ("projected", "used", "total", "budget", "needed")
        )
        budget = _flag(scenario.fake_flags, BUDGET_FLAG)
        last = (earlier[-1].get("duration_seconds") if earlier else None) or 0
        # The stop shows each number to a tenth of a second and the budget to resume with rounded up to a second.
        checks.append(
            Check(
                "stop_arithmetic_holds",
                shown == budget
                and used < budget < total
                and abs(used + projected - total) <= 0.15
                and abs(projected - last) <= 0.051
                and total - 0.05 <= needed < total + 1.05,
                f"stop: projected {projected}, used {used}, total {total}, budget {shown}, resume with {needed}; "
                f"scenario budget {budget}; last Builder attempt {last:.2f} s",
            )
        )
    acknowledged = [
        _when(row.get("at"))
        for row in state.get("pause_acknowledgements") or []
        if isinstance(row, dict) and row.get("pause_status") == PAUSE
    ]
    at = acknowledged[0] if acknowledged else None
    started = [_when(row.get("started_at")) for row in stronger]
    checks.append(
        Check(
            "no_stronger_attempt_before_the_acknowledgement",
            len(earlier) == 2 and bool(stop) and all(at and moment and moment > at for moment in started),
            f"Builder routes {[_route(row) for row in builders]}; stronger attempts started {started}; "
            f"acknowledged {at}",
        )
    )
    checks.append(
        Check(
            "exactly_that_attempt_launched_after_it",
            len(stronger) == 1 and builders[-1] is stronger[0],
            f"Builder routes {[_route(row) for row in builders]}; status {run.get('status')!r}",
        )
    )
    calls = run.get("cli_calls") or []
    granted = _flag(dict(scenario.fake_acknowledge).get(PAUSE) or (), BUDGET_FLAG)
    saved = ((state.get("settings") or {}).get("milestone_checkpoints") or {}).get("max_seconds")
    checks.append(
        Check(
            "acknowledged_once_with_a_budget_that_covers_it",
            calls.count("acknowledge") == 1
            and bool(stop)
            and granted is not None
            and granted >= float(stop["needed"])
            and saved == granted,
            f"CLI calls: {calls}; acknowledged with {granted}; saved budget {saved}; "
            f"stop advised {stop['needed'] if stop else None}",
        )
    )
    view = run.get("view") or {}
    checks.append(
        Check(
            "completed_after_the_pause",
            run.get("status") == "TASK_COMPLETE" and view.get("done") is True,
            f"status {run.get('status')!r}; done {view.get('done')!r}; stop {view.get('stop_reason')!r}"[:400],
        )
    )
    return checks
