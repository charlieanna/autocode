#!/usr/bin/env python3
"""Run AutoCode against realistic engineering scenarios and judge the results.

  python3 scenarios/run.py list
  python3 scenarios/run.py check [ID ...]    # prove each oracle: seed fails, reference passes, broken variants fail
  python3 scenarios/run.py run ID ... --fake  # full AutoCode run with a scripted model (no spend)
  python3 scenarios/run.py run ID ... --profile glm53-openai --i-authorize-live-model-spend
  python3 scenarios/run.py route --fake      # which workflow AutoCode recognizes for each prompt in routing.toml
  python3 scenarios/run.py stats [ID ...]    # runs, passes, pass streak, time and model stages per scenario and mode

Results go to .scenario-runs/<time>-<id>-<mode>/ (result.json, steps.jsonl,
state.json, and the delivered project). See scenarios/README.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog, routing, stats, verdict  # noqa: E402
from harness.driver import (REPO, DriveError, Driver, default_autocode, fake_setup, live_setup, metrics,  # noqa: E402
                            split_by_turn)
from harness.project import materialize  # noqa: E402


def self_test(scenario) -> list[tuple[str, bool, str]]:
    """Each row: (variant, behaved as expected, oracle summary)."""
    rows = []
    with tempfile.TemporaryDirectory(prefix="scenario-check-") as tmp:
        variants = [("seed", None, False)]
        if scenario.reference:
            variants.append(("reference", scenario.reference, True))
        variants += [(f"broken/{path.name}", path, False) for path in scenario.broken]
        for name, overlay, should_pass in variants:
            project = materialize(scenario.seed, Path(tmp) / name.replace("/", "-"),
                                  *([overlay] if overlay else []))
            result = verdict.evaluate(scenario, project)
            ok = not result.error and result.passed == should_pass
            rows.append((name, ok, result.summary))
    return rows


def cmd_check(args) -> int:
    failures = 0
    for scenario in selected(args.ids):
        print(scenario.id)
        missing = scenario.missing_tools()
        if missing:
            print(f"  skip  requires {', '.join(missing)}")
            continue
        if not scenario.reference:
            print("  warn  no reference solution: the oracle is only shown to reject the seed")
        for name, ok, summary in self_test(scenario):
            failures += not ok
            print(f"  {'ok  ' if ok else 'FAIL'}  {name:<28} {summary}")
    return 1 if failures else 0


def cmd_list(args) -> int:
    for scenario in catalog.load_all():
        extras = [label for label, present in (("reference", scenario.reference), ("fake", scenario.fake_check))
                  if present]
        needs = f" requires {','.join(scenario.requires)}" if scenario.requires else ""
        print(f"{scenario.id:<28} {scenario.category:<12} {scenario.title}  [{', '.join(extras)}]{needs}")
    return 0


def require_mode(args) -> None:
    if not args.fake and not args.profile:
        sys.exit("run needs --fake or --profile NAME")
    if args.profile and not args.i_authorize_live_model_spend:
        sys.exit(f"refusing to spend on live models: add --i-authorize-live-model-spend (profile {args.profile})")
    if not args.autocode and importlib.util.find_spec("psutil") is None:
        sys.exit("AutoCode needs psutil, which this Python lacks: run with the project's virtualenv "
                 "(.venv/bin/python scenarios/run.py ...) or pass --autocode")


def cmd_run(args) -> int:
    require_mode(args)
    failures = 0
    for scenario in selected(args.ids):
        result = run_one(scenario, args)
        outcome = result["verdict"]
        note = ""
        if outcome not in (verdict.PASS, verdict.SKIPPED, verdict.NOT_EXERCISED):
            if scenario.known_failure:
                note = f"\n  known failure (not counted): {scenario.known_failure}"
            else:
                failures += 1
        elif outcome == verdict.PASS and scenario.known_failure:
            note = "\n  now passes: remove known_failure from scenario.toml"
        print(f"{scenario.id}: {outcome} — {result['summary']}{note}\n  evidence: {result['evidence']}")
    return 1 if failures else 0


def run_one(scenario, args) -> dict:
    mode = ("fake" if args.fake_solution == "reference" else f"fake-{Path(args.fake_solution).name}") if args.fake else args.profile
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.out / f"{stamp}-{scenario.id}-{mode}").resolve()
    out.mkdir(parents=True)
    result = {"scenario": scenario.id, "title": scenario.title, "category": scenario.category, "mode": mode,
              "autocode": autocode_revision(), "started_at": stamp, "evidence": str(out)}
    skip = [f"requires {tool}" for tool in scenario.missing_tools()]
    solution = scenario.dir / args.fake_solution
    if args.fake and not scenario.fake_check:
        skip.append("no [fake] check in scenario.toml")
    if args.fake and not solution.is_dir():
        skip.append(f"no {args.fake_solution}/ solution for the fake to apply")
    if skip:
        return finish(out, result, verdict.SKIPPED, "; ".join(skip))

    project = materialize(scenario.seed, out / "project")
    flags, env = fake_setup(scenario, out, solution) if args.fake else live_setup(args.profile)
    driver = Driver(project, out, flags, env, autocode=args.autocode or default_autocode(),
                    max_steps=args.max_steps or scenario.max_steps,
                    timeout_seconds=60 * (args.timeout_minutes or scenario.timeout_minutes))
    drive_error = ""
    started = time.monotonic()
    try:
        driver.drive(scenario.brief, scenario.turns)
    except DriveError as error:
        drive_error = str(error)
    wall_seconds = round(time.monotonic() - started, 1)
    state = driver.state()
    if state:
        (out / "state.json").write_text(json.dumps(state, indent=2))
    record = run_record(driver, state)
    oracle = verdict.evaluate(scenario, project, record)
    outcome, summary = verdict.judge(state.get("status", ""), oracle, scenario.expected)
    outcome, summary = verdict.exercised(outcome, summary, scenario.requires_stages, record["model_stages"])
    if drive_error:
        outcome, summary = verdict.ERROR, f"harness stopped: {drive_error}; oracle {oracle.summary}"
    result.update(runner_status=state.get("status"), run_dir=str(driver.run_dir or ""),
                  cli_calls=len(driver.steps), answers=driver.answers, metrics=metrics(state),
                  resolutions=record["resolutions"],
                  wall_seconds=wall_seconds, cli_seconds=round(sum(step["seconds"] for step in driver.steps), 1),
                  workflow=record["view"].get("workflow"), expected=scenario.expected,
                  turns=[{"say": turn["say"], "workflow": turn["view"].get("workflow"),
                          "model_stage_names": turn["model_stages"]} for turn in record.get("turns", [])],
                  checks=[dataclasses.asdict(check) for check in oracle.checks], oracle_error=oracle.error)
    return finish(out, result, outcome, summary)


def run_record(driver: Driver, state: dict) -> dict:
    """What an oracle may know about how the run went (harness.oracle.run_checks).

    A scenario with follow-up turns also gets ``turns``: one record of the same
    shape per turn, the first for the brief, each later one from the moment its
    message was said. Only the last turn's record carries the final view; earlier
    ones carry the view the driver saw when that turn ended.
    """
    view = {}
    if driver.run_dir:
        try:
            view = driver.view()
        except DriveError:
            view = {}
    record = {"status": state.get("status", ""), "view": view, "stages": metrics(state)["stage_names"],
              "model_stages": metrics(state)["model_stage_names"],
              "answers": driver.answers, "cli_calls": [step["kind"] for step in driver.steps],
              # AutoResolver's accepted diagnoses, oldest first, for oracles that score them (issue #59).
              "resolutions": [{"diagnosis": row.get("diagnosis"), "evidence": row.get("evidence")}
                              for row in state.get("resolution_history") or [] if isinstance(row, dict)]}
    if driver.turn_marks:
        stage_turns = split_by_turn(state, driver.turn_marks)
        steps = [0, *(mark["steps"] for mark in driver.turn_marks), len(driver.steps)]
        answers = [0, *(mark["answers"] for mark in driver.turn_marks), len(driver.answers)]
        record["turns"] = []
        for index, stages in enumerate(stage_turns):
            turn_metrics = metrics({"stages": stages})
            record["turns"].append({
                "say": driver.turn_marks[index - 1]["say"] if index else None,
                "stages": turn_metrics["stage_names"], "model_stages": turn_metrics["model_stage_names"],
                "answers": driver.answers[answers[index]:answers[index + 1]],
                "cli_calls": [step["kind"] for step in driver.steps[steps[index]:steps[index + 1]]],
                "view": (driver.turn_marks[index].get("view") if index < len(driver.turn_marks) else view) or {}})
    return record


def cmd_route(args) -> int:
    """Start each prompt in scenarios/routing.toml once and read which workflow AutoCode recognized."""
    require_mode(args)
    table = routing.load()
    seed_scenario = catalog.load(table["seed"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.out / f"{stamp}-routing-{'fake' if args.fake else args.profile}").resolve()
    out.mkdir(parents=True)
    rows = []
    for number, prompt in enumerate(table["prompts"], start=1):
        root = out / f"prompt-{number:02d}"
        project = materialize(seed_scenario.seed, root / "project")
        stand_in = catalog.Scenario(id="routing", dir=seed_scenario.dir, title="Routing check", category="review",
                                    brief=prompt["text"], requires=(), fake_check="true", max_steps=3,
                                    timeout_minutes=args.timeout_minutes or 10)
        flags, env = (fake_setup(stand_in, root, seed_scenario.reference) if args.fake
                      else live_setup(args.profile))
        driver = Driver(project, root, [*flags, "--pause-after-stage"], env,
                        autocode=args.autocode or default_autocode(), max_steps=3,
                        timeout_seconds=60 * stand_in.timeout_minutes)
        recognized, error = None, ""
        try:
            driver.call("start", task=prompt["text"])
            runs = project / ".autocode" / "runs"
            found = sorted(runs.glob("*/state.json")) if runs.is_dir() else []
            if found:
                driver.run_dir = found[-1].parent
                recognized = driver.view().get("workflow")
        except DriveError as failure:
            error = str(failure)
        ok = recognized == prompt["workflow"]
        rows.append({"prompt": prompt["text"], "expected": prompt["workflow"], "recognized": recognized,
                     "ok": ok, "error": error})
        print(f"  {'ok  ' if ok else 'MISS'}  {prompt['workflow']:<8} got {recognized!s:<8} {prompt['text']}"
              + (f"  ({error[:80]})" if error else ""))
    matched = sum(row["ok"] for row in rows)
    (out / "result.json").write_text(json.dumps({"seed": table["seed"], "matched": matched, "total": len(rows),
                                                 "autocode": autocode_revision(), "rows": rows}, indent=2))
    print(f"routing: {matched}/{len(rows)} prompts recognized\n  evidence: {out}")
    if matched == len(rows):
        if table.get("known_failure"):
            print("  now passes: remove known_failure from scenarios/routing.toml")
        return 0
    if table.get("known_failure"):
        print(f"  known failure (not counted): {table['known_failure']}")
        return 0
    return 1


def cmd_stats(args) -> int:
    """Summarize every saved result under --out: how often each scenario ran and passed, and how long it took."""
    rows = stats.summarize(stats.load_results(args.out), ids=set(args.ids), mode=args.mode)
    if not rows:
        print(f"no scenario results under {args.out}")
        return 0
    print(stats.format_table(rows))
    return 0


def finish(out: Path, result: dict, outcome: str, summary: str) -> dict:
    result.update(verdict=outcome, summary=summary)
    (out / "result.json").write_text(json.dumps(result, indent=2))
    return result


def autocode_revision() -> dict:
    def git(*args):
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--", "tools"))}


def selected(ids: list[str]):
    return [catalog.load(i) for i in ids] if ids else catalog.load_all()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list scenarios").set_defaults(func=cmd_list)
    check = commands.add_parser("check", help="prove each scenario's oracle without running AutoCode")
    check.add_argument("ids", nargs="*")
    check.set_defaults(func=cmd_check)
    run = commands.add_parser("run", help="run AutoCode on scenarios and judge the result")
    run.add_argument("ids", nargs="*")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model; no spend")
    run.add_argument("--fake-solution", default="reference", metavar="DIR",
                     help="overlay the fake applies, e.g. broken/special-case to prove FALSE_COMPLETE detection")
    mode.add_argument("--profile", help="live model profile from harness/profiles.py")
    run.add_argument("--i-authorize-live-model-spend", action="store_true")
    run.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    run.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    run.add_argument("--max-steps", type=int, help="override the scenario's CLI call budget")
    run.add_argument("--timeout-minutes", type=int, help="override the scenario's time budget")
    run.set_defaults(func=cmd_run)
    route = commands.add_parser("route", help="check which workflow AutoCode recognizes for each one-line prompt")
    mode = route.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model; no spend")
    mode.add_argument("--profile", help="live model profile from harness/profiles.py")
    route.add_argument("--i-authorize-live-model-spend", action="store_true")
    route.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    route.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    route.add_argument("--timeout-minutes", type=int, help="time budget per prompt (default 10)")
    route.set_defaults(func=cmd_route)
    summary = commands.add_parser("stats", help="runs, passes, pass streak, time and model stages from saved results")
    summary.add_argument("ids", nargs="*")
    summary.add_argument("--mode", help="only this mode: fake, or a live profile name")
    summary.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    summary.set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
