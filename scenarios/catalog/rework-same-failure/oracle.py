"""The greeting repair (checks as in completion-rework-direct) with nothing else changed, and an honest stop when the
suite the brief requires fails the same way twice on a file the task may not change (#611).

In check mode (``run is None``) only the delivery is scored: the greeting is right and VERSION, test_release.py and
the greeting tests are the seed's, so the controls reject the seed and every broken overlay and accept the reference.
With a run it also checks, from the scripted run's trace and the public status view, that:

- the Tester failed the same check every time, and the run ended after the Builder's ladder (an ordinary attempt,
  then the stronger attempt a repeat goes to) with no paid diagnosis and no stage after the last review;
- AutoCode ended the run itself (#2485): the stop names that check, asks the person nothing (no needs, no command)
  and is not a budget stop. Before #2485 the run held for a person to authorize a retry, and its stop stated the
  spend and time left for that decision; no person decides now, so the view's cost carries them;
- the run stopped with budget left: the view's cost shows the scenario's cap and less spent, and the run's active
  time is under its time budget. A loop that keeps spending fails the ladder check.
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
    testers = [row for row in rows if row["stage"] == "tester"]
    checks.append(
        Check(
            "same_check_failed_every_time",
            len(testers) >= 2
            and all(row["command"] == scenario.fake_check and row["exit_code"] not in (0, None) for row in testers),
            repr([(row["command"], row["exit_code"]) for row in testers]),
        )
    )
    stages = [
        stage
        for stage in run.get("model_stages") or []
        if stage in ("builder", "tester", "review_completion", "resolve", "investigate_stuck")
    ]
    # The Builder's ladder: one ordinary attempt, and a repeat goes straight to the stronger attempt (#611);
    # the run then ends. No paid diagnosis, and no stage after the last review. The first build's failing
    # proof also sends it back to the same Builder once before any Tester, which is no ladder step.
    checks.append(
        Check(
            "ended_after_the_ladder_without_a_diagnosis",
            3 <= stages.count("builder") <= 4
            and "investigate_stuck" not in stages
            and bool(stages)
            and stages[-1] == "review_completion",
            f"stages after planning: {stages}",
        )
    )
    reason = str(view.get("stop_reason") or "")
    origin = str((view.get("needs") or {}).get("pause_origin") or "")
    # #2485: AutoCode ends the run itself with a recorded outcome that names the failing check; a person is
    # asked nothing (no needs, no command), and it is not a budget stop.
    checks.append(
        Check(
            "ended_naming_the_check_and_asking_nothing",
            view.get("done") is not True
            and reason.startswith("AutoCode could not finish this task: ")
            and scenario.fake_check in reason
            and view.get("needs") is None
            and not view.get("next_command")
            and not any(re.search(r"(BUDGET|LIMIT|TIMEOUT)$", word) for word in (status, origin)),
            f"status {status!r}; origin {origin!r}; needs {view.get('needs')!r}; stop {reason[:300]!r}",
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
    return checks
