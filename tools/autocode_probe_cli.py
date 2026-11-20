"""`autocode probe`: measure the models you choose for a stage, within a cost cap you set (#3630).

Without ``--max-cost-usd`` it only prints what measuring the chosen models can cost and runs
nothing. With a cap it runs the stage's probe on each model and level in turn
(``autocode_model_probe``): the quick requests first, the rest only for a model that passes them,
each as a spec-first task on a fresh copy of the bundled project, and records the results where
automatic selection and the quota fallback read them (``~/.config/autocode/measured-routes.json``).
``--stage requirements`` (the default) stops each run when Requirements answers. ``--stage planner``
runs each request to plan approval, never given, with the Plan Reviewer the person names
(``--checker``), and grades the plan (``autocode_planner_probe``). ``--stage plan_reviewer`` has the
Planner the person names (``--planner``) write each request's plan once, then has every candidate
review it as written and with a flaw planted, and grades the two verdicts
(``autocode_plan_reviewer_probe``). ``--stage builder`` runs each small task to its end with the
candidate as Builder, the Planner the person names (``--planner``) and a checker of another family
(``--checker``) as Plan Reviewer, Tester and Completion Reviewer, approves the plan on the probe's own
throwaway project, and grades the result by tests the run never saw (``autocode_builder_probe``).
``--stage tester`` and ``--stage completion`` run each request to its end with every other stage
scripted (``probe_script_provider``): a Builder that writes a sound change, or a flawed one first, and
grade whether the candidate sent the flaw back and passed the sound change (``autocode_check_probe``).
Invoking it with a cap is the person's approval of that spend;
nothing else runs a probe.

Layer: interface over autocode_model_probe.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from . import autocode_configure as configure
    from . import autocode_model_probe as model_probe
except ImportError:
    import autocode_configure as configure
    import autocode_model_probe as model_probe

STAGES = tuple(model_probe.STAGES)
DEFAULT_LEVEL = "default"
_STAGE_NAMES = {
    "requirements": "Requirements",
    "planner": "the Planner",
    "plan_reviewer": "the Plan Reviewer",
    "builder": "the Builder",
    "tester": "the Tester",
    "completion": "the Completion Reviewer",
}
_CHECKED = ("planner", "builder")  # stages whose candidates a --checker checks
_PLANNED = ("plan_reviewer", "builder")  # stages whose plans a --planner writes


def parser():
    result = argparse.ArgumentParser(prog="autocode probe", description=__doc__.split("\n\n")[0])
    result.add_argument("--stage", choices=STAGES, default="requirements", help="the stage to measure models for")
    result.add_argument(
        "--measure",
        nargs=2,
        action="append",
        metavar=("MODEL", "LEVEL"),
        required=True,
        help=f"a model to measure and the reasoning level to run it at ('{DEFAULT_LEVEL}' for its own); repeat for each",
    )
    result.add_argument(
        "--max-cost-usd",
        type=_probe_cap,
        help="run the probe, spending at most this much in USD; without it the probe only shows its estimate",
    )
    result.add_argument(
        "--checker",
        nargs=2,
        action="append",
        default=[],
        metavar=("MODEL", "LEVEL"),
        help="planner: the Plan Reviewer that checks every plan; builder: the Plan Reviewer, Tester and "
        "Completion Reviewer of every task; repeat to give one of another family for a candidate that shares "
        "the first one's family",
    )
    result.add_argument(
        "--planner",
        nargs=2,
        metavar=("MODEL", "LEVEL"),
        help="plan_reviewer: the Planner that writes the plans every candidate reviews; builder: the Planner "
        "of every task",
    )
    result.add_argument(
        "--requirements",
        nargs=2,
        metavar=("MODEL", "LEVEL"),
        help="planner, plan_reviewer, builder: the Requirements model and level every run uses "
        "(default: your saved choice)",
    )
    result.add_argument("--provider", help="the provider the probe's runs use, as for a task")
    result.add_argument("--engine", help="the engine the probe's runs use, as for a task")
    result.add_argument("--json", action="store_true", help="print one JSON object")
    return result


def cli(argv) -> int:
    args = parser().parse_args(argv)
    models = [_route(pair) for pair in args.measure]
    checkers = [_route(pair) for pair in args.checker]
    if args.stage in _CHECKED and not checkers:
        print(f"autocode probe: --stage {args.stage} needs --checker MODEL LEVEL", file=sys.stderr)
        return 2
    if args.stage not in _CHECKED and checkers:
        print("autocode probe: --checker applies to --stage planner and builder only", file=sys.stderr)
        return 2
    if args.stage in ("requirements", *model_probe.CHECK_STAGES) and args.requirements:
        print(f"autocode probe: --requirements does not apply to --stage {args.stage}", file=sys.stderr)
        return 2
    if (args.stage in _PLANNED) != bool(args.planner):
        print(
            "autocode probe: --stage plan_reviewer and --stage builder need --planner MODEL LEVEL, "
            "and --planner applies to them only",
            file=sys.stderr,
        )
        return 2
    planner = _route(args.planner) if args.planner else None
    if (
        args.stage == "plan_reviewer"
        and planner is not None
        and all(model_probe.independence.shares(planner[0], model) for model, _level in models)
    ):
        print(
            "autocode probe: every --measure model shares the Planner's family, so none may review its plans",
            file=sys.stderr,
        )
        return 2
    unchecked = [
        model
        for model, _level in models
        if (args.stage == "planner" and not model_probe.checker_for(model, checkers))
        or (args.stage == "builder" and not model_probe.builder_checker_for(model, checkers, planner))
    ]
    if unchecked:
        print(
            "autocode probe: no --checker may check "
            + ", ".join(unchecked)
            + "; add one from another model family"
            + (" than the Planner" if args.stage == "builder" else ""),
            file=sys.stderr,
        )
        return 2
    if args.stage in model_probe.CHECK_STAGES and args.engine == "codex":
        print(
            f"autocode probe: --stage {args.stage} runs every other stage on a scripted tool, which a Codex "
            "run cannot use; measure on OpenCode, Kilo or Claude Code",
            file=sys.stderr,
        )
        return 2
    estimate = model_probe.estimate(models, args.stage)
    if args.max_cost_usd is None:
        _print({"estimate": estimate, "ran": False}, args.json, _estimate_text(estimate))
        return 0
    options = []
    if args.provider:
        options += ["--provider", args.provider]
    if args.engine:
        options += ["--engine", args.engine]
    requirements = _route(args.requirements) if args.requirements else None
    if args.stage == "plan_reviewer":
        outcome = model_probe.run_reviewer_probe(
            models, cap_usd=args.max_cost_usd, planner=planner, requirements=requirements, options=tuple(options)
        )
    else:
        extra = {}
        if args.stage in _CHECKED:
            extra = {"checkers": checkers, "requirements": requirements}
        if args.stage == "builder":
            extra["planner"] = planner
        outcome = model_probe.run_probe(
            models, cap_usd=args.max_cost_usd, stage=args.stage, options=tuple(options), **extra
        )
    _print({"estimate": estimate, "ran": True, **outcome}, args.json, _outcome_text(outcome))
    return 0


def _probe_cap(text):
    """`--max-cost-usd` for a probe: the regular run's finite rule (``autocode_configure.cost_cap``), and more
    than 0, since a probe always runs under a cap."""
    value = configure.cost_cap(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return value


def _route(pair):
    model, level = pair
    return (model, None if level == DEFAULT_LEVEL else level)


def _print(payload, as_json, text):
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else text)


def _estimate_text(estimate):
    lines = [f"Measuring these models for {_STAGE_NAMES[estimate.get('stage', 'requirements')]}:"]
    for row in estimate["models"]:
        cost = f"up to ${row['max_usd']:.2f}" if row["max_usd"] is not None else "cost unknown (no price on record)"
        lines.append(
            f"  {row['model']} at {row['effort'] or 'its default level'}: {row['quick_calls']} to {row['calls']} requests, {cost}"
        )
    total = f"up to ${estimate['max_usd']:.2f}" if estimate["max_usd"] is not None else "an unknown amount"
    lines.append(f"In all: {estimate['quick_calls']} to {estimate['calls']} requests, {total} ({estimate['basis']}).")
    lines.append("Nothing has run. Add --max-cost-usd N to run it with that cap.")
    return "\n".join(lines)


def _outcome_text(outcome):
    unpriced = outcome.get("unpriced_replies") or 0
    if outcome.get("spend_uncertain") or unpriced:
        lines = [f"Spent at most ${outcome['spent_usd']:.2f}; spend is uncertain."]
        lines.append(
            "1 reply had no price on record and was counted at its whole allowance."
            if unpriced == 1
            else f"{unpriced} replies had no price on record and were counted at their whole allowance."
        )
    else:
        lines = [f"Spent ${outcome['spent_usd']:.2f}."]
    for row in outcome["ranking"]:
        mark = "qualified" if row["qualified"] else "not qualified"
        lines.append(f"  {row['model']} at {row['effort'] or 'its default level'}: {mark}, {row['reason']}")
    if outcome["stopped"]:
        lines.append(f"Stopped: {outcome['stopped']}.")
    for row in outcome["unmeasured"]:
        reason = f" ({row['reason']})" if row.get("reason") else ""
        lines.append(f"  not measured: {row['model']} at {row['effort'] or 'its default level'}{reason}")
    for row in outcome.get("partial") or ():
        total = (row.get("summary") or {}).get("total")
        lines.append(
            f"  partial attempt kept apart: {row['model']} at {row.get('effort') or 'its default level'}, "
            f"{total} request{'' if total == 1 else 's'} before the stop; its earlier result, if any, is kept"
        )
    for row in outcome.get("skipped") or ():
        lines.append(f"  request {row['request']} skipped: its plan never reached approval ({row.get('run_status')})")
    if outcome["results"]:
        lines.append("Saved for automatic selection and the quota fallback.")
    return "\n".join(lines)
