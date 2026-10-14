"""The greeting CLI (checks as in greenfield-greeting-cli), delivered after the run's spend reached the
person's cost cap and they raised it (#721).

In check mode (``run is None``) only the product is scored, so the controls reject the seed and the
broken overlay and accept the reference. With a run it also checks that the run paused at the cap,
that only the person's own acknowledgement continued it, and that it then completed under their cap.
"""

import json
from pathlib import Path

from harness import catalog
from harness.oracle import Check

PAUSE, RAISED = "PAUSED_COST_LIMIT", 100


def product_checks(project):
    sibling = catalog.load("greenfield-greeting-cli")
    return sibling.oracle()(project, sibling)


def small_cap(scenario):
    """The cap the scenario's run starts under: the value after --max-cost-usd in its [fake] flags."""
    flags = list(scenario.fake_flags)
    return float(flags[flags.index("--max-cost-usd") + 1])


def _state(run) -> dict:
    path = Path(run["run_dir"]) / "state.json" if run.get("run_dir") else None
    return json.loads(path.read_text()) if path and path.is_file() else {}


def check(project, scenario, run=None):
    checks = product_checks(project)
    if run is None:
        return checks
    state = _state(run)
    escalations = ((state.get("resolver") or {}).get("human_escalations") or {}).values()
    origins = [
        ((row.get("identity") or {}).get("proposal") or {}).get("origin") or {}
        for row in escalations
        if isinstance(row, dict)
    ]
    paused = [origin for origin in origins if origin.get("pause_status") == PAUSE]
    checks.append(
        Check(
            "paused_at_the_cost_cap",
            len(paused) == 1,
            f"escalation origins: {[o.get('pause_status') for o in origins]}",
        )
    )
    calls = run.get("cli_calls") or []
    checks.append(Check("acknowledged_once_by_the_person", calls.count("acknowledge") == 1, f"CLI calls: {calls}"))
    cost = (run.get("view") or {}).get("cost") or {}
    spent, cap = cost.get("spent_usd"), small_cap(scenario)
    checks.append(Check("cap_is_the_person_s", cost.get("cap_usd") == RAISED, f"cost: {cost}"))
    checks.append(
        Check(
            "spend_went_past_the_first_cap",
            isinstance(spent, (int, float)) and spent > cap,
            f"spent {spent!r}; first cap {cap}",
        )
    )
    view = run.get("view") or {}
    checks.append(
        Check(
            "completed_after_the_pause",
            run.get("status") == "TASK_COMPLETE" and view.get("done") is True,
            f"status {run.get('status')!r}; done {view.get('done')!r}",
        )
    )
    return checks
