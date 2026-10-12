"""Project settings for a new run, read from the workspace (#707).

A team keeps the settings it would otherwise type on every new run in ``.autocode/config.toml``,
or, in a Python project, under ``[tool.autocode]`` in ``pyproject.toml``; config.toml wins a key
both set. AutoCode's own ``.autocode/.gitignore`` leaves config.toml trackable
(autocode_workspaces), so the file can be committed and reviewed like any other.

Keys are the flag names without the leading dashes (KEYS): ``engine``, ``test-command``, the role
model and reasoning-effort flags, and the budgets. A flag on the command line wins over the file,
and also over any file key it cannot be combined with (ITERATIONS, ROLE_MODELS); the file wins over the built-in
default. No environment variable sets these keys. Only a new run reads the file: a saved run keeps
the settings it started with, recorded in the run, so changing the file never alters a run in
progress.

Two settings stay out of a project's reach (USER_ONLY): the provider, which runs a command the user
installed, is chosen by --provider, AUTOCODE_PROVIDER or default_provider in the user's
~/.config/autocode/config.toml (autocode_providers); and --task-preflight is the operator's
prerequisite manifest. A file that names either, or a key not in KEYS, is refused before a run
starts, as is a value its flag would refuse.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ENGINE = ("engine",)
MODELS = (
    "single-model",
    "astra-model",
    "terra-model",
    "sol-model",
    "completion-model",
    "glm-model",
    "requirements-model",
    "plan-reviewer-model",
    "resolver-model",
    "investigator-model",
    "builder-strong-model",
)
EFFORTS = (
    "reasoning-effort",
    "astra-reasoning-effort",
    "terra-reasoning-effort",
    "sol-reasoning-effort",
    "completion-reasoning-effort",
    "glm-reasoning-effort",
    "requirements-reasoning-effort",
    "plan-reviewer-reasoning-effort",
    "resolver-reasoning-effort",
    "investigator-reasoning-effort",
)
COMMANDS = ("test-command",)
BUDGETS = (
    "max-iterations",
    "unlimited-iterations",
    "max-seconds",
    "max-stage-seconds",
    "max-idle-seconds",
    "max-tool-seconds",
    "no-progress-limit",
    "max-milestone-seconds",
    "max-milestone-replans",
    "max-milestone-stalled-reviews",
    "max-findings-per-task",
    "max-parallel-builders",
)
KEYS = (*ENGINE, *MODELS, *EFFORTS, *COMMANDS, *BUDGETS)

_PROVIDER = (
    "the provider runs a command you installed, so a project cannot choose it: use --provider, "
    "AUTOCODE_PROVIDER or default_provider in ~/.config/autocode/config.toml"
)
USER_ONLY = {
    "provider": _PROVIDER,
    **{f"{role}-provider": _PROVIDER for role in ("astra", "terra", "sol", "completion")},
    "task-preflight": "the prerequisite manifest is the operator's: pass --task-preflight",
}

# Settings the command line gives in more than one form, by argparse destination. Typing one drops
# the file's keys it cannot be combined with as well as its own, so the command line always wins:
# one iteration ceiling (autocode_args.BUDGET_ARGUMENTS), and --single-model, which
# autocode_configure refuses next to a role model.
ITERATIONS = {"max_iterations", "legacy_iteration_ceiling", "unlimited_iterations"}
ROLE_MODELS = {key.replace("-", "_") for key in MODELS if key != "single-model"}


def paths(workspace) -> tuple[Path, Path]:
    """The two files a project may keep its settings in: pyproject.toml, then .autocode/config.toml."""
    root = Path(workspace)
    return root / "pyproject.toml", root / ".autocode" / "config.toml"


def read(workspace) -> dict[str, tuple[object, str]]:
    """Each setting the workspace's project files set, as key -> (value, where it was set).

    Raises ValueError, naming the file, for invalid TOML, a key outside KEYS or a USER_ONLY key.
    """
    pyproject, config = paths(workspace)
    settings: dict[str, tuple[object, str]] = {}
    if pyproject.is_file():
        table = _load(pyproject).get("tool", {}).get("autocode")
        if table is not None:
            settings.update(_checked(table, f"{pyproject} [tool.autocode]"))
    if config.is_file():
        settings.update(_checked(_load(config), str(config)))
    return settings


def _load(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{path} cannot be read as TOML: {error}") from error


def _checked(table, source: str) -> dict[str, tuple[object, str]]:
    if not isinstance(table, dict):
        raise ValueError(f"{source} must be a table of settings")
    for key in table:
        if key in USER_ONLY:
            raise ValueError(f"{source}: {key} cannot be set by a project; {USER_ONLY[key]}")
        if key not in KEYS:
            raise ValueError(f"{source}: unknown setting {key!r}; a project file sets only {', '.join(KEYS)}")
    return {key: (value, source) for key, value in table.items()}


def for_new_run(parser, args, explicit) -> set[str]:
    """autocode_args.parse: give a new run the project's settings for the flags its command line left
    unset, saying where they came from; return their destinations, which then count as typed (a
    budget the team set is the user's, never a runner default). A saved run reads no file."""
    if args.run_dir is not None or args.task is None:
        return set()
    try:
        applied = apply(parser._option_string_actions, args, explicit, args.workspace)
    except ValueError as error:
        parser.error(str(error))
    by_source: dict[str, list[str]] = {}
    for key, source in applied.items():
        by_source.setdefault(source, []).append(key)
    for source, keys in by_source.items():
        print(f"Project settings from {source}: {', '.join(keys)}", file=sys.stderr)
    return {parser._option_string_actions["--" + key].dest for key in applied}


def apply(actions, args, explicit, workspace) -> dict[str, str]:
    """Set each project setting the command line left unset on ``args``; return key -> where it was set.

    ``actions`` maps option strings to the parser's argparse actions, whose type and choices check
    each value as they would check the flag; ``explicit`` holds the destinations typed on the
    command line. Raises ValueError, naming the file and the key, for a value the flag would refuse.
    """
    settings = read(workspace)
    dropped = set(explicit)
    if dropped & ITERATIONS:
        dropped |= ITERATIONS
    if "single_model" in explicit:
        dropped |= ROLE_MODELS
    if explicit & ROLE_MODELS:
        dropped.add("single_model")
    applied = {}
    for key, (value, source) in settings.items():
        action = actions["--" + key]
        if action.dest in dropped:
            continue
        setattr(args, action.dest, _value(action, key, value, source))
        applied[key] = source
    return applied


def _value(action, key: str, value, source: str):
    if action.nargs == 0:  # a switch such as --unlimited-iterations
        if not isinstance(value, bool):
            raise ValueError(f"{source}: {key} must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{source}: {key} must be a string or a whole number")
    try:
        result = action.type(str(value)) if action.type else str(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source}: {key} = {value!r} is not valid for --{key}") from error
    if action.choices is not None and result not in action.choices:
        raise ValueError(f"{source}: {key} must be one of {', '.join(map(str, action.choices))}")
    return result
