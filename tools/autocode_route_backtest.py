"""Replay saved runs in time order and check the shadow suggestions against the rules (#4251 step 3).

For each saved run, oldest first, the scoreboard is built from the runs before it only, and the suggestion a
new run would have recorded is computed from that run's own settings (``autocode_route_suggestion``). The
same is done once more as if the person had chosen no stage, which is the case a suggestion is for. Nothing
launches and no model is called.

The pass bar was fixed before any result was seen (the plan approved on 2026-11-28). Each check is made here
independently of the code that suggests, so a defect there shows as a failed check, not a quiet agreement:

- no suggestion breaks checker independence (``autocode_verifier_independence``);
- no suggestion could make the producer stronger than its checker: each stands only on a model of the
  running model's family that the price table does not price above it;
- no suggestion is made over a stage the person chose;
- no suggestion rests on fewer than ``MIN_ATTEMPTS`` counted attempts on either route;
- where a suggestion stands, the suggested route's first-time rate over every saved run is not below the
  running route's at the 95% range;
- the stages with too little data to suggest are named, not hidden.

``python3 tools/autocode_route_backtest.py BUNDLES [--json]`` reads every ``*/run/state.json`` under
``BUNDLES`` (unpacked ``autocode bundle`` archives), projects each into the ledger row
``autocode_usage.record`` would write, and prints the report.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import warnings
from pathlib import Path
from typing import Any

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_prices as prices
    from . import autocode_route_scoreboard as scoreboard
    from . import autocode_route_suggestion as suggestion
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_prices as prices
    import autocode_route_scoreboard as scoreboard
    import autocode_route_suggestion as suggestion
    import autocode_verifier_independence as independence

SCHEMA = 1
CHECKS = (
    "independence_breaks",
    "stronger_than_checker",
    "over_person_choice",
    "under_evidence",
    "worse_than_running",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unchosen(settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` as if the person had chosen no stage: every origin the runner's default."""
    found = copy.deepcopy(settings)
    found["route_origins"] = dict.fromkeys(_dict(found.get("roles")), "runner_default")
    found.pop("single_model_mode", None)
    return found


def _price(model: str) -> float | None:
    rate = prices.rate(model)
    try:
        return float(rate["input"]) + float(rate["output"]) if rate else None
    except (KeyError, TypeError, ValueError):
        return None


def _problems(role: str, record: dict[str, Any], settings: dict[str, Any], final: dict[tuple, dict]) -> list[str]:
    """The pass-bar checks one stage's record breaks, judged without the suggesting code's own helpers."""
    pick = record.get("suggested")
    if not pick or record.get("blocked"):
        return []
    found = []
    moved = copy.deepcopy(settings)
    moved["roles"][role]["model"] = pick["model"]
    if any(row["producer"] == role for row in independence.clashes(moved)):
        found.append("independence_breaks")
    ran = record["ran"]["model"]
    if pick["model"] != ran:
        mine, theirs = _price(pick["model"]), _price(ran)
        if (
            model_identity.family(pick["model"]) != model_identity.family(ran)
            or mine is None
            or theirs is None
            or mine > theirs
        ):
            found.append("stronger_than_checker")
    if _dict(settings.get("route_origins")).get(role) in suggestion.PERSON_ORIGINS:
        found.append("over_person_choice")
    running = record.get("running") or {}
    if pick["attempts"] < suggestion.MIN_ATTEMPTS or running.get("attempts", 0) < suggestion.MIN_ATTEMPTS:
        found.append("under_evidence")
    job = suggestion.JUDGED.get(role)
    late_pick = final.get((job, pick["model"], pick["effort"]))
    late_ran = final.get((job, ran, record["ran"]["effort"] or "default"))
    if late_pick and late_ran and late_pick["first_time_range"][1] < late_ran["first_time_range"][0]:
        found.append("worse_than_running")
    return found


def replay(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The backtest over ``entries`` (each ``{"row", "settings"}``), oldest first by the row's ``started_at``."""
    ordered = sorted(entries, key=lambda entry: str(entry["row"].get("started_at") or ""))
    checks = dict.fromkeys(CHECKS, 0)
    cases: dict[str, dict[str, Any]] = {"as_saved": {}, "unchosen": {}}
    reasons: dict[str, dict[str, dict[str, int]]] = {"as_saved": {}, "unchosen": {}}
    last = scoreboard.build([entry["row"] for entry in ordered])
    for index, entry in enumerate(ordered):
        settings = entry["settings"]
        backend = settings.get("provider") or settings.get("engine") or "unknown"
        board = scoreboard.build([earlier["row"] for earlier in ordered[:index]])
        final = {
            (item["job"], item["model"], item["effort"]): item for item in last["routes"] if item["backend"] == backend
        }
        for case, used in (("as_saved", settings), ("unchosen", _unchosen(settings))):
            stages = suggestion.suggest(used, board, backend=backend)["stages"]
            for role, record in stages.items():
                tally = cases[case].setdefault(role, {"runs": 0, "suggested": 0, "blocked": 0})
                tally["runs"] += 1
                if record.get("suggested"):
                    tally["blocked" if record.get("blocked") else "suggested"] += 1
                kind = str(record.get("reason") or "").split(":", 1)[0]
                by_role = reasons[case].setdefault(role, {})
                by_role[kind] = by_role.get(kind, 0) + 1
                for problem in _problems(role, record, used, final):
                    checks[problem] += 1
    thin = sorted(
        {
            (item["backend"], item["job"])
            for item in last["routes"]
            if item["judged_by"] == "next_stage"
            and sum(
                1
                for other in last["routes"]
                if (other["backend"], other["job"]) == (item["backend"], item["job"])
                and other["attempts"] >= suggestion.MIN_ATTEMPTS
            )
            < 2
        }
    )
    return {
        "schema": SCHEMA,
        "runs": len(ordered),
        "counted_runs": last["counted_runs"],
        "checks": checks,
        "passed": not any(checks.values()),
        "cases": cases,
        "reasons": reasons,
        "too_little_data": [{"backend": backend, "job": job} for backend, job in thin],
    }


def project(state_file: Path) -> dict[str, Any]:
    """One saved run's ``{"row", "settings"}``: the ledger row ``autocode_usage.record`` would write for it."""
    try:
        from . import autocode_prices as price_table
        from . import autocode_usage as usage
        from . import autocode_usage_outcomes as outcomes
    except ImportError:
        import autocode_prices as price_table
        import autocode_usage as usage
        import autocode_usage_outcomes as outcomes
    state = json.loads(state_file.read_text(encoding="utf-8"))
    run_dir = state_file.parent
    state["run_dir"] = str(run_dir)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        row = {
            "run": run_dir.parent.name,
            "status": state.get("status"),
            **usage.summary(state),
            **outcomes.ledger_fields(
                state,
                records=usage.finished_records(state),
                priced_rows=usage.stage_rows(state, estimator=price_table.estimate),
                has_running_stage=bool(usage.running_stage(state)),
                has_pending_workers=bool(usage.pending_workers(state)),
                model_of=usage._model,
                run_dir=run_dir,
            ),
        }
    return {"row": row, "settings": _dict(state.get("settings"))}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        f"Route suggestion backtest: {report['runs']} saved runs, {report['counted_runs']} counted "
        f"(live and completed), replayed oldest first.",
        "Pass bar: " + ("passed" if report["passed"] else "FAILED"),
    ]
    lines += [f"  {name}: {count}" for name, count in report["checks"].items()]
    for case, title in (("as_saved", "As saved"), ("unchosen", "As if no stage had been chosen")):
        lines.append(f"{title}:")
        for role, tally in sorted(report["cases"][case].items()):
            why = ", ".join(f"{count} {kind}" for kind, count in sorted(report["reasons"][case][role].items()))
            lines.append(f"  {role}: {tally['suggested']} suggested, {tally['blocked']} blocked ({why})")
    if report["too_little_data"]:
        names = ", ".join(f"{item['job']} on {item['backend']}" for item in report["too_little_data"])
        lines.append(f"Too little data to compare models: {names} (fewer than two routes with enough attempts).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parsed = argparse.ArgumentParser(prog="autocode_route_backtest", description=__doc__.split("\n\n")[0])
    parsed.add_argument("bundles", type=Path, help="A folder of unpacked autocode bundles")
    parsed.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parsed.parse_args(argv)
    entries = [project(path) for path in sorted(args.bundles.glob("**/run/state.json"))]
    report = replay(entries)
    print(json.dumps(report, indent=2) if args.json else format_text(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
