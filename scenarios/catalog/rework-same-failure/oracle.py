"""The greeting repair (checks as in completion-rework-direct) with nothing else changed, and an honest stop when the
suite the brief requires fails the same way twice on a file the task may not change (#611).

In check mode (``run is None``) only the delivery is scored: the greeting is right and VERSION, test_release.py and
the greeting tests are the seed's, so the controls reject the seed and every broken overlay and accept the reference.
With a run it also checks, from the scripted run's trace and the public status view, that:

- the Tester ran the same check twice and it failed both times, and the run then stopped without a third Builder
  attempt or a paid diagnosis;
- the stop names that check and says there was no progress on it, and is not a budget stop;
- the run stopped with budget left: the view's cost shows the scenario's cap and less spent, and the run's active
  time is under its time budget. A scripted run never comes near either bound, so this reads only that the stop
  reports what is left. A loop that keeps spending fails the first item's checks: a third attempt or a diagnosis;
- the stop's own text states that spend and that cap, as the view's cost has them, and the run's active time and
  its time limit, as the run saved them, so a person deciding whether to retry sees what is left without asking for
  the status.
"""

import hashlib
import json
import re
import runpy
from pathlib import Path

from harness.oracle import Check

CAP_FLAG = "--max-cost-usd"
OWNED = "greet.py"
SKIP = (".git", ".autocode", "__pycache__")


def product_checks(project, scenario):
    sibling = runpy.run_path(str(Path(__file__).resolve().parents[1] / "completion-rework-direct" / "oracle.py"))
    return sibling["check"](project, scenario)


def _files(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and not any(part in SKIP for part in path.relative_to(root).parts)
    }


def _flag(flags, name):
    flags = list(flags)
    return float(flags[flags.index(name) + 1]) if name in flags[:-1] else None


def _state(run) -> dict:
    path = Path(run["run_dir"]) / "state.json" if run.get("run_dir") else None
    return json.loads(path.read_text()) if path and path.is_file() else {}


def check(project, scenario, run=None):
    checks = product_checks(project, scenario)
    seed, delivered = _files(scenario.seed), _files(project)
    changed = sorted(path for path in set(seed) | set(delivered) if seed.get(path) != delivered.get(path))
    checks.append(Check("only_greet_py_changed", set(changed) <= {OWNED}, f"changed: {changed}"))
    if run is None:
        return checks
    view = run.get("view") or {}
    status = str(run.get("status") or "")
    trace = project.parent / "rework-trace.jsonl"
    rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.is_file() else []
    testers = [row for row in rows if row["stage"] == "sol"]
    checks.append(
        Check(
            "same_check_failed_twice",
            len(testers) == 2
            and all(row["command"] == scenario.fake_check and row["exit_code"] not in (0, None) for row in testers),
            repr([(row["command"], row["exit_code"]) for row in testers]),
        )
    )
    stages = [
        stage for stage in run.get("model_stages") or [] if stage in ("terra", "sol", "astra_review", "astra_resolve")
    ]
    checks.append(
        Check(
            "stopped_without_another_attempt",
            stages == ["terra", "sol", "astra_review"] * 2,
            f"stages after planning: {stages}",
        )
    )
    reason = str(view.get("stop_reason") or "")
    origin = str((view.get("needs") or {}).get("pause_origin") or "")
    checks.append(
        Check(
            "stop_names_the_check_and_no_progress",
            view.get("done") is not True
            and f"({scenario.fake_check})" in reason
            and "no causal progress for the same incident" in reason.lower()
            and not any(re.search(r"(BUDGET|LIMIT|TIMEOUT)$", word) for word in (status, origin)),
            f"status {status!r}; origin {origin!r}; stop {reason[:300]!r}",
        )
    )
    cost = view.get("cost") or {}
    cap = _flag(scenario.fake_flags, CAP_FLAG)
    spent = cost.get("spent_usd")
    state = _state(run)
    active = state.get("active_seconds")
    allowed = ((state.get("settings") or {}).get("limits") or {}).get("max_seconds")
    checks.append(
        Check(
            "stopped_with_budget_left",
            cap is not None
            and cost.get("cap_usd") == cap
            and isinstance(spent, (int, float))
            and spent < cap
            and isinstance(active, (int, float))
            and isinstance(allowed, (int, float))
            and active < allowed,
            f"cost spent {spent} of cap {cost.get('cap_usd')} (scenario {cap}); active {active} s of {allowed} s",
        )
    )
    stated = re.search(r"Cost: (?:at least )?\$([\d,]+\.\d+) so far.*?; cap \$([\d,]+\.\d+)\.", reason)
    checks.append(
        Check(
            "stop_states_spend_and_cap",
            stated is not None
            and cap is not None
            and isinstance(spent, (int, float))
            and abs(float(stated[1].replace(",", "")) - spent) <= 0.005 + 1e-9
            and float(stated[2].replace(",", "")) == cap,
            f"stop {stated[0] if stated else reason[-300:]!r}; view spent {spent}, cap {cost.get('cap_usd')}",
        )
    )
    # The stop shows seconds to one decimal place; no stage runs after the hold, so the saved time is the stated one.
    timed = re.search(r"Active time: ([\d,.]+) s of the ([\d,.]+) s limit\.", reason)
    checks.append(
        Check(
            "stop_states_active_time_and_limit",
            timed is not None
            and isinstance(active, (int, float))
            and isinstance(allowed, (int, float))
            and abs(float(timed[1].replace(",", "")) - active) <= 0.05 + 1e-9
            and float(timed[2].replace(",", "")) == allowed,
            f"stop {timed[0] if timed else reason[-300:]!r}; saved active {active} s of {allowed} s",
        )
    )
    return checks
