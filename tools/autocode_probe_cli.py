"""`autocode probe`: measure the models you choose for a stage, within a cost cap you set (#3630).

Without ``--max-cost-usd`` it only prints what measuring the chosen models can cost and runs
nothing. With a cap it runs the stage's probe on each model and level in turn
(``autocode_model_probe``): the quick requests first, the rest only for a model that passes them,
each as a spec-first task on a fresh copy of the bundled project, and records the results where
automatic selection and the quota fallback read them (``~/.config/autocode/measured-routes.json``).
``--stage requirements`` (the default) stops each run when Requirements answers. ``--stage planner``
runs each request to plan approval, never given, with the Plan Reviewer the person names
(``--checker``), and grades the plan (``autocode_planner_probe``). Invoking it with a cap is the person's approval of
that spend; nothing else runs a probe.

Layer: interface over autocode_model_probe.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from . import autocode_model_probe as model_probe
except ImportError:
    import autocode_model_probe as model_probe

STAGES = tuple(model_probe.STAGES)
DEFAULT_LEVEL = "default"
_STAGE_NAMES = {"requirements": "Requirements", "planner": "the Planner"}


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
        type=float,
        help="run the probe, spending at most this much in USD; without it the probe only shows its estimate",
    )
    result.add_argument(
        "--checker",
        nargs=2,
        action="append",
        default=[],
        metavar=("MODEL", "LEVEL"),
        help="planner: the Plan Reviewer that checks every plan; repeat to give one of another family for a "
        "candidate that shares the first one's family",
    )
    result.add_argument(
        "--requirements",
        nargs=2,
        metavar=("MODEL", "LEVEL"),
        help="planner: the Requirements model and level every run uses (default: your saved choice)",
    )
    result.add_argument("--provider", help="the provider the probe's runs use, as for a task")
    result.add_argument("--engine", help="the engine the probe's runs use, as for a task")
    result.add_argument("--json", action="store_true", help="print one JSON object")
    return result


def cli(argv) -> int:
    args = parser().parse_args(argv)
    models = [_route(pair) for pair in args.measure]
    checkers = [_route(pair) for pair in args.checker]
    if args.stage == "planner" and not checkers:
        print("autocode probe: --stage planner needs --checker MODEL LEVEL for the Plan Reviewer", file=sys.stderr)
        return 2
    if args.stage != "planner" and (checkers or args.requirements):
        print("autocode probe: --checker and --requirements apply to --stage planner only", file=sys.stderr)
        return 2
    unchecked = [
        model for model, _level in models if args.stage == "planner" and not model_probe.checker_for(model, checkers)
    ]
    if unchecked:
        print(
            "autocode probe: no --checker may review " + ", ".join(unchecked) + "; add one from another model family",
            file=sys.stderr,
        )
        return 2
    estimate = model_probe.estimate(models, args.stage)
    if args.max_cost_usd is None:
        _print({"estimate": estimate, "ran": False}, args.json, _estimate_text(estimate))
        return 0
    if args.max_cost_usd <= 0:
        print("autocode probe: --max-cost-usd must be more than 0", file=sys.stderr)
        return 2
    options = []
    if args.provider:
        options += ["--provider", args.provider]
    if args.engine:
        options += ["--engine", args.engine]
    extra = {}
    if args.stage == "planner":
        extra = {"checkers": checkers, "requirements": _route(args.requirements) if args.requirements else None}
    outcome = model_probe.run_probe(
        models, cap_usd=args.max_cost_usd, stage=args.stage, options=tuple(options), **extra
    )
    _print({"estimate": estimate, "ran": True, **outcome}, args.json, _outcome_text(outcome))
    return 0


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
    lines = [f"Spent ${outcome['spent_usd']:.2f}."]
    if outcome["unpriced_replies"]:
        lines.append(f"{outcome['unpriced_replies']} replies had no price on record and are not counted.")
    for row in outcome["ranking"]:
        mark = "qualified" if row["qualified"] else "not qualified"
        lines.append(f"  {row['model']} at {row['effort'] or 'its default level'}: {mark}, {row['reason']}")
    if outcome["stopped"]:
        lines.append(f"Stopped: {outcome['stopped']}.")
    for row in outcome["unmeasured"]:
        reason = f" ({row['reason']})" if row.get("reason") else ""
        lines.append(f"  not measured: {row['model']} at {row['effort'] or 'its default level'}{reason}")
    if outcome["results"]:
        lines.append("Saved for automatic selection and the quota fallback.")
    return "\n".join(lines)
