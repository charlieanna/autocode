"""`autocode stages`: see and change the model and reasoning level each stage of a new task runs.

The person chooses once (``autocode setup`` asks, or ``autocode stages choose``) and AutoCode keeps
the choice for every new task, per backend (owner decision 2026-10-30, autocode_stage_choices).
With no action it lists each stage: what it does, the model and level it runs and whether that is
the person's choice or the default. ``choose`` walks the stages in a terminal, one numbered list
per stage with advice on what suits it. ``set`` and ``reset`` change one stage without asking, and
``--json`` returns the view clients render as one dropdown per stage, with the ``set`` and ``reset``
argument arrays they take.

Layer: interface over autocode_stage_choices. It launches no model and reads no credential.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    from . import autocode_connected_engine as connected_engine
    from . import autocode_project_config as project_config
    from . import autocode_providers as providers
    from . import autocode_route_fit as route_fit
    from . import autocode_stage_choices as stage_choices
except ImportError:
    import autocode_connected_engine as connected_engine
    import autocode_project_config as project_config
    import autocode_providers as providers
    import autocode_route_fit as route_fit
    import autocode_stage_choices as stage_choices

# The job names a person may type for a stage, beside the route names the view uses.
ALIASES = {
    "planner": "glm",
    "plan-reviewer": "plan_reviewer",
    "builder": "terra",
    "tester": "sol",
    "completion-reviewer": "completion",
    "resolver": "astra",
}
STAGE_NAMES = (*stage_choices.ORDER, *ALIASES)


def parser():
    result = argparse.ArgumentParser(prog="autocode stages", description=__doc__.split("\n\n")[0])
    result.add_argument(
        "action", nargs="?", choices=("list", "choose", "set", "reset"), default="list", help="What to do"
    )
    result.add_argument("stage", nargs="?", help=f"The stage to set or reset: {', '.join(STAGE_NAMES)}")
    result.add_argument("--model", help="set: the model the stage runs, one the backend offers")
    result.add_argument(
        "--effort",
        help=f"set: its reasoning level, one the model takes ({stage_choices.OWN_DEFAULT} when it lists none)",
    )
    result.add_argument(
        "--backend",
        help="Claude Code (claude), Codex (codex), OpenCode (opencode) or Kilo (kilocode); default: new runs'",
    )
    result.add_argument("--workspace", default=".", help="The project whose engine setting counts (default: here)")
    result.add_argument("--json", action="store_true", help="Print the stage view clients read")
    return result


def current_backend(workspace=".", *, choose=None) -> str:
    """The backend a new run in ``workspace`` takes, as the runner resolves it: the project's engine,
    else ``AUTOCODE_PROVIDER`` or ``default_provider``, else the connected backend, else OpenCode."""
    try:
        engine = project_config.read(workspace).get("engine", (None, None))[0]
    except (OSError, ValueError):
        engine = None
    if engine in ("codex", "claude"):
        return engine
    default, origin = providers.default_choice()
    if origin != "built-in default":
        return default
    chosen = (choose or connected_engine.choose)()
    return chosen["id"] if chosen else "opencode"


def text(current) -> str:
    lines = [f"Stages on {current['label']} (your choices are kept in {current['file']}):"]
    for stage in current["stages"]:
        lines.append(f"  {stage['label']}: {_route(stage)}")
        lines.append(f"      {stage['does']}")
    if current["options_stop"]:
        lines.append(f"Models cannot be listed: {current['options_stop']}")
    lines.append("Change them with: autocode stages choose")
    return "\n".join(lines)


def _route(stage) -> str:
    chosen = stage["chosen"]
    if chosen:
        return f"{chosen['model']} at {chosen['effort'] or 'its own default level'} (your choice)"
    default = stage["default"]
    if default["model"] and not default["offered"]:
        return f"chosen from your accounts when a task starts ({default['model']} is not offered to them)"
    return f"{default['model']} at {default['effort'] or 'its own default level'} (default)"


def choose(backend, workspace=".", *, ask=input, out=print, file=None, resolve=None, discover=None) -> bool:
    """Ask for every stage in turn and save the answers together; True when they were saved.

    Enter keeps the bracketed model and level: the stage's choice, else its default, else the first
    model the stage may run. A stage is asked after the stages whose work it checks, so its list
    leaves out their models.
    """
    current = stage_choices.view(backend, workspace, file=file, resolve=resolve, discover=discover)
    if current["options_stop"]:
        out(f"Cannot list {current['label']}'s models: {current['options_stop']}")
        return False
    if not current["stages"][0]["options"]:
        out(f"{current['label']} offers no model to your accounts yet; connect one, then run `autocode stages choose`.")
        return False
    out(f"Choose the model and reasoning level for each stage on {current['label']}. Enter keeps the one in brackets.")
    picks: dict = {}
    for stage in current["stages"]:
        role = stage["id"]
        decided = {other: model for other, (model, _effort) in picks.items()}
        rows = [
            (option, stage_choices.unavailable(role, option["model"], decided, backend=backend))
            for option in stage["options"]
        ]
        free = [option for option, why in rows if why is None]
        if not free:
            out(f"No model your accounts offer can run the {stage['label']} beside the stages chosen above.")
            return False
        keep, keep_effort = _keep(stage, free)
        out("")
        out(f"{stage['label']}: {stage['does']}")
        out(f"  Advice: {stage['advice']}")
        for number, (option, why) in enumerate(rows, 1):
            out(f"  {number}) {option['model']}{_facts(option)}" + (f"  (not available: {why})" if why else ""))
        model = _ask_model(stage, rows, keep, ask=ask, out=out)
        option = next(option for option, _why in rows if option["model"] == model)
        default_effort = keep_effort if model == keep["model"] else _nearest(stage, option)
        picks[role] = (model, _ask_effort(option, default_effort, ask=ask, out=out))
    saved = stage_choices.choose(backend, picks, workspace, file=file, resolve=resolve, discover=discover)
    out("")
    out(text(saved))
    return True


def _keep(stage, free):
    by_model = {option["model"]: option for option in free}
    for route in (stage["chosen"], stage["default"]):
        if route and route.get("model") in by_model:
            option = by_model[route["model"]]
            effort = route.get("effort") or stage_choices.OWN_DEFAULT
            choices = stage_choices.effort_choices(option)
            return option, effort if effort in choices else _nearest(stage, option)
    return free[0], _nearest(stage, free[0])


def _nearest(stage, option) -> str:
    """The level the stage's default names, moved to one the option takes."""
    choices = stage_choices.effort_choices(option)
    wanted = stage["default"].get("effort") or "medium"
    return route_fit.nearest(wanted, [level for level in choices if level != stage_choices.OWN_DEFAULT]) or choices[0]


def _facts(option) -> str:
    facts = [option["family"]] if option.get("family") else []
    if option.get("context"):
        facts.append(f"{option['context'] // 1000}k context")
    if option.get("account"):
        facts.append(option["account"] + (f", {option['billing']}" if option.get("billing") else ""))
    return f" ({'; '.join(facts)})" if facts else ""


def _ask_model(stage, rows, keep, *, ask, out) -> str:
    keep_number = next(number for number, (option, _why) in enumerate(rows, 1) if option is keep)
    while True:
        answer = ask(f"  {stage['label']} model [{keep_number}]: ").strip()
        if not answer:
            return keep["model"]
        found = [
            (option, why) for number, (option, why) in enumerate(rows, 1) if answer in (str(number), option["model"])
        ]
        if not found:
            out(f"  Type a number from 1 to {len(rows)}.")
            continue
        option, why = found[0]
        if why:
            out(f"  {option['model']} is not available here: {why}.")
            continue
        return option["model"]


def _ask_effort(option, default, *, ask, out) -> str:
    choices = stage_choices.effort_choices(option)
    if choices == [stage_choices.OWN_DEFAULT]:
        out(f"  {option['model']} lists no reasoning levels; it runs at its own default.")
        return stage_choices.OWN_DEFAULT
    while True:
        answer = ask(f"  Reasoning level ({', '.join(choices)}) [{default}]: ").strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
        out(f"  {option['model']} takes {', '.join(choices)}.")


def _terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _stage(name):
    return ALIASES.get(name.replace("_", "-"), name) if name else None


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    stage = _stage(args.stage)
    try:
        backend = args.backend or current_backend(args.workspace)
        if args.action == "choose":
            if args.json or not _terminal():
                print("autocode stages choose asks in a terminal; use `autocode stages set` elsewhere", file=sys.stderr)
                return 2
            try:
                return 0 if choose(backend, args.workspace) else 1
            except (KeyboardInterrupt, EOFError):
                print("\nNothing was saved.")
                return 130
        if args.action == "set":
            if not stage or not args.model or not args.effort:
                print("autocode stages set needs a stage, --model and --effort", file=sys.stderr)
                return 2
            current = stage_choices.choose(backend, {stage: (args.model, args.effort)}, args.workspace)
        elif args.action == "reset":
            current = stage_choices.reset(backend, stage, args.workspace)
        else:
            current = stage_choices.view(backend, args.workspace)
    except ValueError as error:
        print(f"autocode stages: {error}", file=sys.stderr)
        return 2
    print(json.dumps(current) if args.json else text(current))
    return 0
