#!/usr/bin/env python3
"""Run AutoCode against realistic engineering scenarios and judge the results.

  python3 scenarios/run.py list
  python3 scenarios/run.py check [ID ...]    # prove each oracle: seed fails, reference passes, broken variants fail
  python3 scenarios/run.py run ID ... --fake  # full AutoCode run with a scripted model (no spend)
  python3 scenarios/run.py run ID ... --profile glm53-openai --i-authorize-live-model-spend
  python3 scenarios/run.py run ID ... --profile glm53-mimo --provider kilocode --i-authorize-live-model-spend
  python3 scenarios/run.py run ID ... --fake --hybrid   # rehearse the scenario's [hybrid] route (no spend)
  python3 scenarios/run.py route --fake      # which workflow AutoCode recognizes for each prompt in routing.toml
  python3 scenarios/run.py compare ID ... --fake  # AutoCode vs a plain agent, same oracle (scripted; no spend)
  python3 scenarios/run.py compare ID ... --profile openai-only --baseline opencode --i-authorize-live-model-spend
  python3 scenarios/run.py stats [ID ...]    # runs, passes, pass streak, time and model stages per scenario and mode
  python3 scenarios/run.py plan-compare --fake  # plan planning.toml's requests today vs --adaptive-planning, up to approval

Results go to .scenario-runs/<time>-<id>-<mode>/ (result.json, steps.jsonl,
state.json, and the delivered project); a comparison adds comparison.md and
comparison.json. See scenarios/README.md.
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

from harness import (attempts, baseline, build_compare, catalog, compare, hybrid, plan_compare, profiles, routing,  # noqa: E402
                     stats, verdict)
from harness.driver import (REPO, DriveError, Driver, InterruptedDrive, TurnNotReached, default_autocode, fake_setup,  # noqa: E402
                            live_setup, metrics, changed_between, split_by_turn, workspace_files)
from harness.program_driver import ProgramDriver  # noqa: E402
from harness import components_driver  # noqa: E402
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
            base = ([catalog.load(scenario.components_architecture).reference]
                    if scenario.category == "components" else [])
            if scenario.category == "components" and name.startswith("broken/"):
                base.append(scenario.reference)
            project = materialize(scenario.seed, Path(tmp) / name.replace("/", "-"),
                                  *base, *([overlay] if overlay else []))
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
    if getattr(args, "provider", None) and not args.profile:
        sys.exit("--provider changes a live profile's provider: use it with --profile NAME")
    if importlib.util.find_spec("psutil") is None:
        sys.exit("Scenario process supervision needs psutil, which this Python lacks: run with the project's "
                 "virtualenv (.venv/bin/python scenarios/run.py ...), including for custom --autocode commands")


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
        split = result.get("hybrid") or {}
        if "live_stage_names" in split:
            note += (f"\n  hybrid: scripted {', '.join(split['scripted_stage_names']) or 'nothing'}; "
                     f"live ({split['live_tool']}) {', '.join(split['live_stage_names']) or 'nothing'}")
        diagnosis = result.get("diagnosis")
        if diagnosis:
            note += f"\n  diagnosis: {diagnosis['verdict']} — {diagnosis.get('reason', '')}"
        print(f"{scenario.id}: {outcome} — {result['summary']}{note}\n  evidence: {result['evidence']}")
    return 1 if failures else 0


def caps_flags(args) -> list[str]:
    """Run-budget caps forwarded to AutoCode on every launch, so no live run is unbounded."""
    caps = []
    for name in ("max_seconds", "max_stage_seconds", "max_iterations"):
        value = getattr(args, name, None)
        if value is not None:
            caps += [f"--{name.replace('_', '-')}", str(value)]
    return caps


def evidence_directory(root: Path, label: str) -> tuple[str, Path]:
    """Allocate fresh evidence atomically, including simultaneous same-scenario runs."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return stamp, Path(tempfile.mkdtemp(prefix=f"{stamp}-{label}-", dir=root))


def interrupted_result(out, result, driver, started, error):
    """Retain a cutoff without grading or starting any further invocation."""
    result.update(harness_error=str(error), diagnosis=None, oracle_passed=None,
                  usage_status="unknown", cli_calls=len(driver.steps), answers=driver.answers,
                  wall_seconds=round(time.monotonic() - started, 1), rejected_model_calls=None,
                  metrics={"model_seconds": None, "model_stages": None, "report_repairs": None, "tokens": None})
    return finish(out, result, verdict.INTERRUPTED_UNGRADED, str(error))


def run_one(scenario, args, *, extra_flags=(), extra_env=None) -> dict:
    provider = getattr(args, "provider", None)
    split = getattr(args, "hybrid", False)  # the scenario's [hybrid] route: some stages scripted, the rest live
    mode = (("fake" if args.fake_solution == "reference" else f"fake-{Path(args.fake_solution).name}") if args.fake
            else f"{args.profile}-via-{provider}" if provider else args.profile)
    mode = hybrid.mode(mode) if split else mode  # never counted with natural runs (harness/hybrid.py)
    if scenario.category == "components" and getattr(args, "local_docker", False):
        mode += "-local-docker"
    stamp, out = evidence_directory(args.out, f"{scenario.id}-{mode}")
    result = {"scenario": scenario.id, "title": scenario.title, "category": scenario.category, "mode": mode,
              "autocode": autocode_revision(), "started_at": stamp, "evidence": str(out)}
    comparison = getattr(args, "attempt_context", None)
    if comparison is not None:
        result.update(repeat=comparison["repeat"], variant=comparison["variant"])
    if not args.fake:
        result["profile"] = profiles.with_provider(profiles.resolve(args.profile), provider)
    skip = [f"requires {tool}" for tool in scenario.missing_tools()]
    if scenario.category == "components" and not args.fake and not getattr(args, "local_docker", False):
        skip.append("a live components scenario requires explicit --local-docker")
    solution = scenario.dir / args.fake_solution
    if (args.fake or split) and not scenario.fake_check:
        skip.append("no [fake] check in scenario.toml")
    if args.fake and scenario.fake_live_calls and not getattr(args, "i_authorize_live_model_spend", False):
        skip.append("its Investigator is a real model: add --i-authorize-live-model-spend")
    # Under a live profile no stage is scripted, so the fault it needs is never injected: three live runs of
    # stuck-planner-citation (2026-09-30) were judged FALSE_COMPLETE on checks that could not have passed.
    if not args.fake and not split and scenario.fake_live_calls:
        skip.append("hybrid scenario: only its Investigator is live; run it with --fake --i-authorize-live-model-spend")
    if (args.fake or split) and not solution.is_dir():
        skip.append(f"no {args.fake_solution}/ solution for the fake to apply")
    if split and scenario.category == "program":
        skip.append("a program runs every workstream through `autocode program`; it has no hybrid route")
    if split and scenario.category == "components":
        skip.append("a components scenario has separate architecture and component runs; no hybrid route")
    if split and not skip:
        live_flags = None if args.fake else live_setup(args.profile, provider)[0]
        try:
            flags, env, route = hybrid.setup(scenario, out, solution, live_flags=live_flags)
        except hybrid.Unavailable as error:
            skip.append(f"hybrid: {error}")
        result["hybrid"] = hybrid.route(scenario)
    if skip:
        result["diagnosis"] = None  # nothing ran, so nothing was diagnosed (scenarios/README.md, "Diagnosis")
        return finish(out, result, verdict.SKIPPED, "; ".join(skip))

    attempts.admit(out, result, {"max_steps": args.max_steps or scenario.max_steps,
                                "timeout_seconds": 60 * (args.timeout_minutes or scenario.timeout_minutes),
                                **{name: getattr(args, name, None)
                                   for name in ("max_seconds", "max_stage_seconds", "max_iterations")}})
    project = materialize(scenario.seed, out / "project")
    if not split:
        flags, env = fake_setup(scenario, out, solution) if args.fake else live_setup(args.profile, provider)
    flags = [*flags, *caps_flags(args), *extra_flags]
    env = {**env, **(extra_env or {})}
    if scenario.category == "components":
        return run_components(scenario, args, out, result, project, flags, env)
    if scenario.category == "program":
        return run_program(scenario, args, out, result, project, flags, env)
    driver = Driver(project, out, flags, env, autocode=args.autocode or default_autocode(),
                    max_steps=args.max_steps or scenario.max_steps,
                    timeout_seconds=60 * (args.timeout_minutes or scenario.timeout_minutes),
                    explicit_answers=scenario.fake_answers if args.fake and not split else ())
    drive_error, not_reached = "", None
    started = time.monotonic()
    try:
        driver.drive(scenario.brief, scenario.turns)
    except InterruptedDrive as error:
        # An oracle cannot grade an owner-loss cutoff. Preserve the attempt,
        # project and existing receipts without launching another CLI or model.
        return interrupted_result(out, result, driver, started, error)
    except TurnNotReached as error:
        not_reached = error  # AutoCode stopped before a turn could be said: judged, never PASS
    except DriveError as error:
        drive_error = str(error)
    wall_seconds = round(time.monotonic() - started, 1)
    state = driver.state()
    if state:
        (out / "state.json").write_text(json.dumps(state, indent=2))
    try:
        record = run_record(driver, state)
    except InterruptedDrive as error:
        return interrupted_result(out, result, driver, started, error)
    live_stages = record["model_stages"]
    if split:
        served = hybrid.calls(out)
        # A scripted call is no model's: the diagnosis does not count it, nor does requires_stages.
        record["scripted_outputs"] = hybrid.scripted_outputs(served)
        result["hybrid"] = hybrid.block(route, served, state)
        live_stages = result["hybrid"]["live_stage_names"]
    oracle = verdict.evaluate(scenario, project, record)
    # Scored apart from the run verdict; only its NOT_EXERCISED reaches it (scenarios/README.md, "Diagnosis").
    diagnosis = verdict.diagnose(scenario, project, record)
    unexercised = ((diagnosis.get("reason") or "not exercised")
                   if diagnosis and diagnosis.get("verdict") == verdict.NOT_EXERCISED else "")
    outcome, summary = verdict.judge(state.get("status", ""), oracle, scenario.expected)
    if not_reached:
        outcome, summary = verdict.turn_not_reached(outcome, summary, not_reached.turn)
    outcome, summary = verdict.exercised(outcome, summary, scenario.requires_stages, live_stages, unexercised)
    if drive_error:
        outcome, summary = verdict.ERROR, f"harness stopped: {drive_error}; oracle {oracle.summary}"
    result.update(runner_status=state.get("status"), run_dir=str(driver.run_dir or ""),
                  harness_error=drive_error, turn_not_reached=str(not_reached or ""), oracle_passed=oracle.passed,
                  cli_calls=len(driver.steps), answers=driver.answers, metrics=metrics(state),
                  resolutions=record["resolutions"],
                  wall_seconds=wall_seconds, cli_seconds=round(sum(step["seconds"] for step in driver.steps), 1),
                  workflow=record["view"].get("workflow"), expected=scenario.expected,
                  turns=[{"say": turn["say"], "workflow": turn["view"].get("workflow"),
                          "model_stage_names": turn["model_stages"]} for turn in record.get("turns", [])],
                  checks=[dataclasses.asdict(check) for check in oracle.checks], oracle_error=oracle.error,
                  diagnosis=diagnosis)
    usage = record["view"].get("usage")
    if isinstance(usage, dict):
        result.update(usage_snapshot=usage,
                      usage_status="recorded" if (usage.get("cost_usd") or {}).get("complete") else "unknown")
    return finish(out, result, outcome, summary)


def run_components(scenario, args, out, result, project, flags, env):
    """Judge the integrated product, never a model's PASS or an interrupted invocation."""
    started = time.monotonic()
    try:
        driver, target, summary, steps = components_driver.drive(scenario, args, out, project, flags, env)
    except InterruptedDrive as error:
        result.update(oracle_passed=None, usage_status="unknown", wall_seconds=round(time.monotonic() - started, 1))
        return finish(out, result, verdict.INTERRUPTED_UNGRADED, str(error))
    except (DriveError, OSError, ValueError, RuntimeError) as error:
        return finish(out, result, verdict.ERROR, f"components harness stopped: {error}")
    architecture_metrics = components_driver.component_metrics({"components": {"architecture": {"view": driver.view()}}})
    stage_metrics = components_driver.component_metrics(summary)
    record = {"components": summary, "model_stages": [*architecture_metrics["model_stage_names"],
                                                       *stage_metrics["model_stage_names"]],
              "local_docker": getattr(args, "local_docker", False)}
    oracle = verdict.evaluate(scenario, target, record)
    status = "TASK_COMPLETE" if summary.get("exit_code") == 0 else "PAUSED_LOCAL_SMOKE"
    outcome, text = verdict.judge(status, oracle, scenario.expected)
    result.update(runner_status=status, product=str(target), run_dir=str(driver.run_dir),
                  components=summary, local_docker=record["local_docker"], oracle_passed=oracle.passed,
                  wall_seconds=round(time.monotonic() - started, 1), cli_calls=len(driver.steps) + len(steps),
                  metrics={**stage_metrics, "architecture": architecture_metrics},
                  checks=[dataclasses.asdict(check) for check in oracle.checks], oracle_error=oracle.error)
    return finish(out, result, outcome, text)


def run_program(scenario, args, out: Path, result: dict, project: Path, flags: list[str], env: dict) -> dict:
    """A program scenario (scenarios/README.md, "Programs"): driven through `autocode program` and judged on
    the program's product, its integration worktree, with the program's own stops (verdict.judge_program)."""
    driver = ProgramDriver(scenario, project, out, flags, env, autocode=args.autocode or default_autocode(),
                           max_steps=args.max_steps or scenario.max_steps,
                           timeout_seconds=60 * (args.timeout_minutes or scenario.timeout_minutes),
                           explicit_answers=scenario.fake_answers if args.fake else ())
    drive_error = ""
    started = time.monotonic()
    try:
        driver.drive()
    except DriveError as error:
        drive_error = str(error)
    wall_seconds = round(time.monotonic() - started, 1)
    summary = driver.finish()
    record = driver.record()
    product = driver.product()
    oracle = verdict.evaluate(scenario, product, record)
    diagnosis = verdict.diagnose(scenario, product, record)
    unexercised = ((diagnosis.get("reason") or "not exercised")
                   if diagnosis and diagnosis.get("verdict") == verdict.NOT_EXERCISED else "")
    status = record["status"]
    # Until the agreement exists the plan run is all there is, and it is judged as a run.
    outcome, text = (verdict.judge_program(status, oracle, scenario.expected, summary) if summary
                     else verdict.judge(status, oracle, scenario.expected))
    outcome, text = verdict.change_not_reached(outcome, text, record["changes"])
    outcome, text = verdict.exercised(outcome, text, scenario.requires_stages, record["model_stages"], unexercised)
    if drive_error:
        outcome, text = verdict.ERROR, f"harness stopped: {drive_error}; oracle {oracle.summary}"
    rows = summary.get("workstreams") or []
    result.update(runner_status=status, product=str(product), run_dir=str(driver.plan.run_dir or ""),
                  harness_error=drive_error, oracle_passed=oracle.passed, cli_calls=len(driver.steps),
                  answers=driver.answers, metrics=record["metrics"], resolutions=[], wall_seconds=wall_seconds,
                  cli_seconds=round(sum(step["seconds"] for step in driver.steps), 1),
                  workflow=record["plan"]["view"].get("workflow"), expected=scenario.expected,
                  checks=[dataclasses.asdict(check) for check in oracle.checks], oracle_error=oracle.error,
                  diagnosis=diagnosis,
                  program={"agreement": summary.get("agreement"), "tokens": record["agreement"],
                           "workstream_ids": record["workstream_ids"],
                           "integration_branch": summary.get("integration_branch"),
                           "skeleton": summary.get("skeleton"), "journeys": summary.get("journeys"),
                           "change_requests": summary.get("change_requests"), "changes": record["changes"],
                           "verifications": record["verifications"],
                           "workstreams": [{key: row.get(key) for key in
                                            ("id", "kind", "status", "run_status", "run_dir", "verified_checks",
                                             "merged_under", "retired_runs", "blocked_reason")} for row in rows],
                           "runs": {wid: [{key: run[key] for key in ("run_dir", "retired", "status", "workflow")}
                                          | {"model_stages": len(run["model_stages"])} for run in runs]
                                    for wid, runs in record["children"].items()}})
    return finish(out, result, outcome, text)


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
        except InterruptedDrive:
            raise
        except DriveError:
            view = {}
    record = {"status": state.get("status", ""), "view": view, "stages": metrics(state)["stage_names"],
              "model_stages": metrics(state)["model_stage_names"],
              "answers": driver.answers, "cli_calls": [step["kind"] for step in driver.steps],
              "steps": [{"kind": step["kind"], "exit": step["exit"]} for step in driver.steps],
              # AutoResolver's accepted diagnoses, oldest first, for oracles that score them (issue #59).
              "resolutions": [{"diagnosis": row.get("diagnosis"), "evidence": row.get("evidence")}
                              for row in state.get("resolution_history") or [] if isinstance(row, dict)]}
    if driver.turn_marks:
        stage_turns = split_by_turn(state, driver.turn_marks)
        steps = [0, *(mark["steps"] for mark in driver.turn_marks), len(driver.steps)]
        answers = [0, *(mark["answers"] for mark in driver.turn_marks), len(driver.answers)]
        record["turns"] = []
        # Workspace snapshots at the start, at each follow-up and now (None where a record has none).
        files = [getattr(driver, "start_files", None), *(mark.get("files") for mark in driver.turn_marks),
                 workspace_files(driver.project) if getattr(driver, "project", None) else None]
        # Each turn's changed files as it left them: earlier turns were kept when the next was said.
        kept = [mark.get("kept") for mark in driver.turn_marks]
        last = len(driver.turn_marks)
        kept.append(str(driver.keep_turn_files(last, files[last], files[last + 1]))
                    if files[last] is not None and files[last + 1] is not None and hasattr(driver, "keep_turn_files")
                    else None)
        for index, stages in enumerate(stage_turns):
            turn_metrics = metrics({"stages": stages})
            record["turns"].append({
                "say": driver.turn_marks[index - 1]["say"] if index else None,
                "stages": turn_metrics["stage_names"], "model_stages": turn_metrics["model_stage_names"],
                "answers": driver.answers[answers[index]:answers[index + 1]],
                "cli_calls": [step["kind"] for step in driver.steps[steps[index]:steps[index + 1]]],
                "steps": [{"kind": step["kind"], "exit": step["exit"]}
                          for step in driver.steps[steps[index]:steps[index + 1]]],
                # What this turn changed in the workspace, read from disk before and after it.
                "changed_files": (changed_between(files[index], files[index + 1])
                                  if files[index] is not None and files[index + 1] is not None else None),
                # A directory holding those files as this turn left them (Driver.keep_turn_files).
                "kept_files": kept[index],
                "view": (driver.turn_marks[index].get("view") if index < len(driver.turn_marks) else view) or {}})
    return record


def cmd_route(args) -> int:
    """Start each prompt in scenarios/routing.toml once and read which workflow AutoCode recognized."""
    require_mode(args)
    table = routing.load()
    seed_scenario = catalog.load(table["seed"])
    _, out = evidence_directory(args.out, f"routing-{'fake' if args.fake else args.profile}")
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


def cmd_plan_compare(args) -> int:
    """Plan each request in scenarios/planning.toml with today's pipeline and with adaptive planning."""
    cases = plan_compare.load()
    if args.rebuild:
        records = plan_compare.rebuild(cases, args.rebuild)
        print((args.rebuild / "comparison.md").read_text())
        return 1 if any(record["error"] for record in records.values()) else 0
    require_mode(args)
    if args.ids:
        unknown = set(args.ids) - {case.id for case in cases}
        if unknown:
            sys.exit(f"unknown planning cases: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.id in args.ids]
    _, out = evidence_directory(args.out, f"plan-compare-{'fake' if args.fake else args.profile}")
    (out / "run.json").write_text(json.dumps({"autocode": autocode_revision(), "mode": "fake" if args.fake else args.profile,
                                              "profile": None if args.fake else profiles.resolve(args.profile),
                                              "cases": [case.id for case in cases]}, indent=2))
    records = plan_compare.run(cases, out, jobs=args.jobs, fake=args.fake, profile=args.profile,
                               autocode=args.autocode or default_autocode(),
                               timeout_minutes=args.timeout_minutes or 45, max_steps=args.max_steps or 40)
    print((out / "comparison.md").read_text())
    print(f"evidence: {out}")
    return 1 if any(record["error"] for record in records.values()) else 0


def cmd_build_compare(args) -> int:
    """Repeat complete scenarios with fixed and adaptive planning, preserving every attempt."""
    if args.rebuild:
        report = build_compare.rebuild(args.rebuild)
        print((args.rebuild / "comparison.md").read_text())
    else:
        if not args.prepare:
            require_mode(args)
        elif not args.fake and not args.profile:
            sys.exit("--prepare needs --fake or --profile NAME")
        if args.repeats < 1 or args.jobs < 1:
            sys.exit("--repeats and --jobs must be positive")
        _, out = evidence_directory(args.out, f"build-compare-{'fake' if args.fake else args.profile}")
        scenarios = []
        for scenario in selected(args.ids):
            if scenario.category in ("program", "components"):
                # Fixed and adaptive planning compare one run's plan; a program plans once and runs many.
                print(f"{scenario.id}: skipped (a {scenario.category} scenario; build-compare compares single runs)")
            else:
                scenarios.append(scenario)
        if args.prepare:
            protocol = build_compare.prepare(scenarios, args, out, revision=autocode_revision())
            print(f"Prepared {2 * len(protocol['pairs'])} attempts; no model calls\n  protocol: {out / 'protocol.json'}")
            return 0
        report = build_compare.run(scenarios, args, out, run_one=run_one, revision=autocode_revision())
        print((out / "comparison.md").read_text())
        print(f"evidence: {out}")
    return 0 if report["all_passed"] else 1


def cmd_compare(args) -> int:
    """Run each scenario through AutoCode and through a plain agent, and judge both with the same oracle."""
    require_mode(args)
    if not args.fake:
        probe = baseline.command(args.baseline, args.baseline_command, Path("."), None)
        if not baseline.available(probe):
            sys.exit(f"baseline agent {probe[0]!r} is not installed; install it or pass --baseline-command")
    mode = "fake" if args.fake else args.profile
    stamp, root = evidence_directory(args.out, f"compare-{mode}")
    autocode_args = argparse.Namespace(**{**vars(args), "out": root})
    rows = []
    for scenario in selected(args.ids):
        solution = scenario.dir / args.fake_baseline_solution
        if scenario.category in ("program", "components"):
            rows.append({"scenario": scenario.id, "skipped": "a multi-run scenario delivers its integrated product through "
                         "several runs; a plain agent has no equivalent"})
            print(f"{scenario.id}: skipped ({rows[-1]['skipped']})")
            continue
        if args.fake and not solution.is_dir():
            rows.append({"scenario": scenario.id, "skipped": f"no {args.fake_baseline_solution}/ for the fake agent"})
            print(f"{scenario.id}: skipped ({rows[-1]['skipped']})")
            continue
        started = time.monotonic()
        auto = run_one(scenario, autocode_args)
        seconds = round(time.monotonic() - started, 1)
        if auto["verdict"] == verdict.SKIPPED:
            rows.append({"scenario": scenario.id, "skipped": auto["summary"]})
            print(f"{scenario.id}: skipped ({auto['summary']})")
            continue
        # The deliverable alone, as the baseline is judged: no AutoCode-only process checks.
        delivered = verdict.evaluate(scenario, Path(auto["evidence"]) / "project")
        base = run_baseline(scenario, args, root, solution)
        row = {"scenario": scenario.id, "category": scenario.category, "expected": scenario.expected,
               "autocode": {"verdict": auto["verdict"], "summary": auto["summary"], "seconds": seconds,
                            "deliverable_passed": delivered.passed, "deliverable": delivered.summary,
                            "model_stages": len((auto.get("metrics") or {}).get("model_stage_names") or []),
                            "tokens": (auto.get("metrics") or {}).get("tokens"), "evidence": auto["evidence"]},
               "baseline": base}
        rows.append(row)
        print(f"{scenario.id}: AutoCode {auto['verdict']} ({seconds}s), baseline {base['verdict']} "
              f"({base['seconds']}s); deliverable accepted: {compare.outcome(row)}")
    summary = compare.summarize(rows)
    meta = {"mode": mode, "baseline": "a scripted agent" if args.fake else
            (f"`{args.baseline_command}`" if args.baseline_command else f"{args.baseline} (one call)"),
            "autocode": autocode_revision(), "started_at": stamp, "out": str(root)}
    (root / "comparison.json").write_text(json.dumps({**meta, "summary": summary, "rows": rows}, indent=2))
    (root / "comparison.md").write_text(compare.markdown(meta, rows, summary))
    a, b = summary["autocode"], summary["baseline"]
    print(f"compared {summary['compared']} scenarios: deliverable accepted AutoCode {a['deliverable_passed']}, "
          f"baseline {b['deliverable_passed']}; false completions AutoCode {a['false_completions']}, "
          f"baseline {b['false_completions']}\n  report: {root / 'comparison.md'}")
    return 0


def run_baseline(scenario, args, root: Path, solution: Path) -> dict:
    out = root / f"{scenario.id}-baseline"
    out.mkdir()
    project = materialize(scenario.seed, out / "project")
    if args.fake:
        argv, env = baseline.fake_setup(out, solution)
    else:
        builder = (profiles.resolve(args.profile).get("models") or {}).get("builder")
        model = args.baseline_model or (builder if args.baseline == "opencode" and not args.baseline_command else None)
        argv, env = baseline.command(args.baseline, args.baseline_command, project, model), {}
    call = baseline.run(argv, project, scenario.brief, out / "agent.log", env=env,
                        timeout_seconds=60 * (args.timeout_minutes or scenario.timeout_minutes))
    oracle = verdict.evaluate(scenario, project)
    outcome, summary = verdict.judge(call["status"], oracle, scenario.expected)
    result = {**call, "verdict": outcome, "summary": summary.replace("AutoCode", "the agent"),
              "deliverable_passed": oracle.passed, "deliverable": oracle.summary, "evidence": str(out),
              "checks": [dataclasses.asdict(check) for check in oracle.checks], "oracle_error": oracle.error}
    (out / "result.json").write_text(json.dumps({"scenario": scenario.id, **result}, indent=2))
    return result


def cmd_stats(args) -> int:
    """Summarize every saved result under --out: how often each scenario ran and passed, and how long it took."""
    rows = stats.summarize(stats.load_results(args.out), ids=set(args.ids), mode=args.mode)
    if not rows:
        print(f"no scenario results under {args.out}")
        return 0
    print(stats.format_table(rows))
    return 0


def finish(out: Path, result: dict, outcome: str, summary: str) -> dict:
    result.update(verdict=outcome, summary=summary,
                  # Secondary class for campaigns (#455); the verdict stays the contract.
                  stop_class=verdict.stop_class(outcome, result.get("status") or result.get("runner_status") or "",
                                                summary))
    attempts.atomic_json(out / "result.json", result, max_bytes=None)
    attempts.finish(out, outcome)
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
    run.add_argument("--provider", help="run the profile's models through this provider instead "
                                        "(e.g. kilocode, or a tool set up in docs/providers.md)")
    run.add_argument("--hybrid", action="store_true",
                     help="script the stages the scenario's [hybrid] route names with its fake fault and run the rest "
                          "on --profile (a tool registered with a TOML file), or on a scripted stand-in with --fake; "
                          "mode NAME-hybrid")
    run.add_argument("--i-authorize-live-model-spend", action="store_true")
    run.add_argument("--local-docker", action="store_true",
                     help="explicit opt-in to build/run/tear down real local Docker containers for components")
    run.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    run.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    run.add_argument("--max-steps", type=int, help="override the scenario's CLI call budget")
    run.add_argument("--timeout-minutes", type=int, help="override the scenario's time budget")
    run.add_argument("--max-seconds", type=int, help="forwarded to AutoCode: total active provider time")
    run.add_argument("--max-stage-seconds", type=int, help="forwarded to AutoCode: per-stage time cap")
    run.add_argument("--max-iterations", type=int, help="forwarded to AutoCode: iteration ceiling")
    run.set_defaults(func=cmd_run)
    comparison = commands.add_parser("compare", help="run AutoCode and a plain agent on the same scenarios; "
                                                     "judge both with the same oracle")
    comparison.add_argument("ids", nargs="*")
    mode = comparison.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model and scripted agent; no spend")
    comparison.add_argument("--fake-solution", default="reference", metavar="DIR",
                            help="overlay the fake AutoCode provider applies")
    comparison.add_argument("--fake-baseline-solution", default="reference", metavar="DIR",
                            help="overlay the fake agent applies, e.g. broken/special-case")
    mode.add_argument("--profile", help="live model profile for AutoCode, from harness/profiles.py")
    comparison.add_argument("--baseline", default="opencode", choices=sorted(baseline.PRESETS),
                            help="plain agent to compare against (default: opencode, AutoCode's default engine)")
    comparison.add_argument("--baseline-model", help="model for the agent (default: the profile's builder model "
                                                     "for opencode; the agent's own default otherwise)")
    comparison.add_argument("--baseline-command", help="custom agent command; {project} and {model} are "
                                                       "substituted and the brief is sent on stdin")
    comparison.add_argument("--i-authorize-live-model-spend", action="store_true")
    comparison.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    comparison.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    comparison.add_argument("--max-steps", type=int, help="override the scenario's CLI call budget")
    comparison.add_argument("--timeout-minutes", type=int, help="override the time budget, for both sides")
    comparison.set_defaults(func=cmd_compare)
    route = commands.add_parser("route", help="check which workflow AutoCode recognizes for each one-line prompt")
    mode = route.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model; no spend")
    mode.add_argument("--profile", help="live model profile from harness/profiles.py")
    route.add_argument("--i-authorize-live-model-spend", action="store_true")
    route.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    route.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    route.add_argument("--timeout-minutes", type=int, help="time budget per prompt (default 10)")
    route.set_defaults(func=cmd_route)
    planning = commands.add_parser("plan-compare", help="plan planning.toml's requests with today's pipeline and "
                                   "with --adaptive-planning, stopping at plan approval")
    planning.add_argument("ids", nargs="*", help="case ids from scenarios/planning.toml (default: all)")
    mode = planning.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model; no spend")
    mode.add_argument("--profile", help="live model profile from harness/profiles.py")
    planning.add_argument("--i-authorize-live-model-spend", action="store_true")
    planning.add_argument("--jobs", type=int, default=1, help="planning runs at a time (default 1)")
    planning.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    planning.add_argument("--autocode", nargs="+", help="AutoCode command to test (default: this checkout)")
    planning.add_argument("--timeout-minutes", type=int, help="time budget per planning run (default 45)")
    planning.add_argument("--max-steps", type=int, help="CLI call budget per planning run (default 40)")
    planning.add_argument("--rebuild", type=Path, metavar="DIR", help="rewrite DIR's comparison from its runs' "
                          "record.json files (for a comparison that was cut short); runs nothing")
    planning.set_defaults(func=cmd_plan_compare)

    builds = commands.add_parser("build-compare", help="repeat complete scenarios with fixed and adaptive "
                                   "planning; judge deliveries and retain failed attempts")
    builds.add_argument("ids", nargs="*", help="scenario ids (default: all; original briefs and oracles)")
    mode = builds.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="scripted model; no spend")
    mode.add_argument("--profile", help="explicit live model profile from harness/profiles.py")
    builds.add_argument("--fake-solution", default="reference", metavar="DIR")
    builds.add_argument("--i-authorize-live-model-spend", action="store_true")
    builds.add_argument("--repeats", type=int, default=2, help="pairs per scenario (default 2)")
    builds.add_argument("--jobs", type=int, default=1, help="pairs at a time; arms within a pair run sequentially")
    builds.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    builds.add_argument("--autocode", nargs="+")
    builds.add_argument("--max-steps", type=int)
    builds.add_argument("--timeout-minutes", type=int)
    builds.add_argument("--max-seconds", type=int)
    builds.add_argument("--max-stage-seconds", type=int)
    builds.add_argument("--max-iterations", type=int)
    builds.add_argument("--rate-card", type=Path, help="frozen public API rates JSON; required for live comparisons")
    builds.add_argument("--prepare", action="store_true", help="save the exact protocol without model calls")
    builds.add_argument("--rebuild", type=Path, metavar="DIR", help="rebuild reports from saved attempts; no model calls")
    builds.set_defaults(func=cmd_build_compare)

    summary = commands.add_parser("stats", help="runs, passes, pass streak, time and model stages from saved results")
    summary.add_argument("ids", nargs="*")
    summary.add_argument("--mode", help="only this mode: fake, a live profile name, or either with -hybrid")
    summary.add_argument("--out", type=Path, default=REPO / ".scenario-runs")
    summary.set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
