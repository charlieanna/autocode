"""The one table from AutoCode's old code names to the names of the jobs they do.

Owner decision 2026-11-10: the code names ``astra``, ``glm``, ``terra`` and ``sol`` are replaced
by job names everywhere, in four steps. This module is the first: it names every old spelling and its replacement once, so the
flags, the stage choices and later the run's routes and stages read one list. The old flags keep
working as aliases of the new ones. A run saved before the rename is refused at resume, as a run
saved at a retired stage is (#3010), and its status and reports stay readable.

The words are role and stage names only. The same letters are also parts of real model names
(``gpt-6-sol``, ``gpt-5.6-terra``, ``gpt-6-astra``, ``glm-5.3``), which never change.

Pure: imports nothing from AutoCode, so any layer may read it.
"""

from __future__ import annotations

# Configured route (``settings.roles`` key) -> its job name.
ROUTES = {
    "glm": "planner",
    "astra": "task_planner",
    "terra": "builder",
    "sol": "tester",
}

# The old route names this runtime no longer runs: a run saved with one is refused at resume
# (autocode_workflows.retired_stage). Steps 2 and 3 renamed them, and the stages with them.
RETIRED_ROUTES = ("glm", "astra", "terra", "sol")

# Stage code name -> its job name. The v2 planning flow already runs ``plan``, ``plan_review``,
# ``plan_revise`` and ``plan_finalize``, so the legacy flow's new names do not reuse them.
STAGES = {
    "astra_discovery": "draft_plan",
    "astra_challenge": "review_plan",
    "glm_revise": "revise_plan",
    "astra_finalize": "finalize_plan",
    "astra_plan": "plan_task",
    "terra": "builder",
    "sol": "tester",
    "astra_review": "review_completion",
    "astra_checkpoint": "review_checkpoint",
    "astra_resolve": "resolve",
    "astra_diagnose": "diagnose",
}

# Every flag spelled with an old route name. ``--investigator-*`` already names the stuck
# Investigator's route, and the Design Reviewer takes no flag of its own.
OLD_FLAGS = (
    "--glm-model",
    "--glm-reasoning-effort",
    "--astra-model",
    "--astra-reasoning-effort",
    "--astra-provider",
    "--terra-model",
    "--terra-reasoning-effort",
    "--terra-provider",
    "--sol-model",
    "--sol-reasoning-effort",
    "--sol-provider",
)


def route(name: str) -> str:
    """The job name of a route, old or new; an unknown name is returned as it is."""
    return ROUTES.get(name, name)


def current_route(name: str) -> str:
    """The name this runtime gives a route spelled old or new: a retired name becomes its job name."""
    return ROUTES[name] if name in RETIRED_ROUTES else name


def stage(name: str) -> str:
    """The job name of a stage, old or new; an unknown name is returned as it is."""
    return STAGES.get(name, name)


def flag(old: str) -> str:
    """The new spelling of an old flag (``--terra-model`` -> ``--builder-model``); any other as it is."""
    if old not in OLD_FLAGS:
        return old
    name, _, suffix = old.removeprefix("--").partition("-")
    return f"--{ROUTES[name].replace('_', '-')}-{suffix}"


# New flag spelling -> the old one it stands for.
FLAG_ALIASES = {flag(old): old for old in OLD_FLAGS}


def spellings(name: str) -> tuple[str, ...]:
    """Every spelling of a flag, old or new: its new name first, then the old one; other flags alone."""
    if name in FLAG_ALIASES:
        return (name, FLAG_ALIASES[name])
    new = flag(name)
    return (new, name) if new != name else (name,)


def legacy_flag(word: str) -> str:
    """The old spelling of a new flag (keeping ``=VALUE``), so code that still matches old names finds it."""
    name, separator, value = word.partition("=")
    return FLAG_ALIASES.get(name, name) + separator + value


def renamed_routes(table: dict, where: str) -> dict:
    """``table`` keyed by routes with each retired route name replaced by its job name.

    A provider config or another file the person keeps may still spell a route the old way; it
    keeps working. One route spelled both ways is refused, since neither spelling can win.
    """
    out: dict = {}
    for key, value in table.items():
        new = current_route(key)
        if new in out:
            raise ValueError(f"{where} names the {new} route twice (as {new} and as its old name)")
        out[new] = value
    return out


def file_stem(route: str) -> str:
    """The stem of a route's schema file, which keeps the old name until the files are renamed."""
    return next((old for old in RETIRED_ROUTES if ROUTES[old] == route), route)


# The stages that run on the per-task Planner's route: the ones once named ``astra_*``.
TASK_PLANNER_STAGES = frozenset(new for old, new in STAGES.items() if old.startswith("astra"))


def task_planner_stage(stage) -> bool:
    """Whether ``stage`` (or its report repair) runs on the per-task Planner's route."""
    return str(stage or "").removesuffix("_report_repair") in TASK_PLANNER_STAGES


def stage_route(stage) -> str:
    """The route a stage record ran on when the record names none, read from the stage's own name."""
    base = str(stage or "").removesuffix("_report_repair")
    if base in TASK_PLANNER_STAGES:
        return "task_planner"
    return "planner" if base == "revise_plan" else base
