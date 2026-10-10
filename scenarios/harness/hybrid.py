"""Hybrid runs (issue #59): a live profile with chosen stages scripted by the fake provider and its fault.

Some traps a real model rarely walks into. In six live claude-tiers runs of feature-stock-refusals no Builder
wrote the vacuous refusal tests, so AutoResolver was never tested on them. Such a scenario declares a route in
its scenario.toml:

    [hybrid]
    scripted = ["recognize_workflow", "astra_discovery", ...]   # every call scripted
    first_attempt = ["terra"]                                     # only the stage's first call scripted

``run --profile NAME --hybrid`` then runs the scenario live with those calls answered by the scripted provider
(fake_codex.py with the scenario's [fake] fault) and every other call by the profile's own tool, so the planted
failure is reached by construction while the stage under test is a real model. AutoCode sees one
config-registered tool named ``hybrid`` (hybrid_stage.py), written for this run under its evidence directory
and found through XDG_CONFIG_HOME: the user's provider configs are read, never written. The live tool must be
one registered with a TOML file and ``output = "report_file"`` (examples/claude-provider): the hybrid tool
copies its roles, model list, version command, login checks and Builder retry policy (``[builder_retry]``), and
runs its command unchanged.

``run --fake --hybrid`` rehearses a route with no spend: a stand-in tool that runs the fake provider takes the
live side, so it proves the routing and the labels, nothing about a model.

Hybrid results never mix with natural ones: their mode is ``<profile>-hybrid`` (``fake-hybrid``), result.json
has a ``hybrid`` block (the route, and every call with the side that served it), and the run record names the
reports the scripted side wrote (``scripted_outputs``). A diagnosis counts only the calls the live side served
(harness/resolver_calls.py), and ``requires_stages`` is met only by a live stage.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

from . import profiles, resolver_calls
from .driver import fake_setup

STAGE_SCRIPT = Path(__file__).resolve().parent / "hybrid_stage.py"
NAME = "hybrid"  # the tool AutoCode runs a hybrid run on
STANDIN = "standin"  # the live side of a rehearsal (--fake --hybrid)
# The stand-in's models: one per role, every verifier a different family from its producer, as AutoCode requires.
STANDIN_MODELS = {role: f"standin-{role}" for role in profiles.ROLES}
# AutoCode's tool roles (tools/providers/command.py REQUIRED_ROLES) and the profile role each one is.
TOOL_ROLES = {
    "astra": "resolver",
    "terra": "builder",
    "sol": "validator",
    "completion": "completion",
    "glm": "planner",
    "plan_reviewer": "reviewer",
}


class Unavailable(RuntimeError):
    """This run cannot be hybrid: the reason is the SKIPPED summary."""


def route(scenario) -> dict:
    return {"scripted": list(scenario.hybrid_scripted), "first_attempt": list(scenario.hybrid_first_attempt)}


def mode(base: str) -> str:
    return f"{base}-hybrid"


def user_tool(name: str) -> tuple[dict, Path]:
    """The live tool's own config (~/.config/autocode/providers/<name>.toml, as AutoCode finds it), checked to be
    one the hybrid tool can stand in front of."""
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = root / "autocode" / "providers" / f"{name}.toml"
    if not path.is_file():
        raise Unavailable(
            f"hybrid runs put a scripted stage in front of a tool registered with a TOML file; no "
            f"{path} (built-in OpenCode and Codex cannot be split by stage)"
        )
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise Unavailable(f"{path} is not valid TOML: {error}") from None
    if config.get("output", "report_file") != "report_file" or config.get("resume") or config.get("sandbox_adapter"):
        raise Unavailable(
            f"hybrid runs need a report_file tool without sessions or a sandbox adapter; {path} is not one"
        )
    return config, path


def standin_tool(fake: list[str]) -> dict:
    """A tool that runs the fake provider, taking the live side in a rehearsal."""
    return {
        "name": STANDIN,
        "command": [*fake, "exec", "--model", "{model}", "--output-schema", "{schema}", "-o", "{report}"],
        "prompt": "stdin",
        "models": sorted(set(STANDIN_MODELS.values())),
        "version_command": [sys.executable, "--version"],
        "roles": {tool: {"model": STANDIN_MODELS[role], "effort": "medium"} for tool, role in TOOL_ROLES.items()},
    }


def setup(scenario, out: Path, solution: Path, *, live_flags: list[str] | None) -> tuple[list[str], dict, dict]:
    """Write this run's hybrid tool and route; return AutoCode's flags and environment and the route record.
    ``live_flags`` are the live profile's flags (its ``--provider`` names the live tool); None rehearses with the
    stand-in. Raises Unavailable when the scenario or the live tool cannot be run this way."""
    plan = route(scenario)
    if not any(plan.values()):
        raise Unavailable("no [hybrid] route in scenario.toml")
    if scenario.fake_live_calls:
        raise Unavailable("its scripted run already makes a live call; it has no hybrid route")
    fake = [sys.executable, str(out / "bin" / "codex")]
    if live_flags is None:
        live, live_name = standin_tool(fake), STANDIN
        flags = profiles.flags({"provider": NAME, "models": STANDIN_MODELS})
    else:
        live_name = live_flags[live_flags.index("--provider") + 1]
        live, _ = user_tool(live_name)
        flags = list(live_flags)
        flags[flags.index("--provider") + 1] = NAME
    _, fake_env = fake_setup(scenario, out, solution)
    shutil.copy2(STAGE_SCRIPT, out / "bin" / "hybrid_stage.py")
    root = out / "hybrid"
    tools = root / "config" / "autocode" / "providers"
    tools.mkdir(parents=True)
    # The live side runs with the environment the harness started from; the scripted side also knows its config.
    restored = {"XDG_CONFIG_HOME": os.environ.get("XDG_CONFIG_HOME")}
    config = fake_env["SCENARIO_FAKE_CONFIG"]
    scripted_env = {**restored, "SCENARIO_FAKE_CONFIG": config, "SCENARIO_FAKE_SIDE": "scripted"}
    live_env = {
        **restored,
        **({"SCENARIO_FAKE_CONFIG": config, "SCENARIO_FAKE_SIDE": "live"} if live_flags is None else {}),
    }
    record = {
        **plan,
        "live_tool": live_name,
        "prompt": live.get("prompt", "stdin"),
        "trace": str(root / "trace.jsonl"),
        "sides": {
            "scripted": {"command": fake, "env": scripted_env},
            "live": {"command": live["command"], "env": live_env},
        },
    }
    (root / "route.json").write_text(json.dumps(record, indent=2))
    command = [
        sys.executable,
        str(out / "bin" / "hybrid_stage.py"),
        str(root / "route.json"),
        *(
            f"{{{value}}}"
            for value in (
                "workspace",
                "sandbox",
                "model",
                "effort",
                "schema",
                "report",
                "role",
                "run_dir",
                "prompt_file",
            )
        ),
    ]
    (tools / f"{NAME}.toml").write_text(toml(tool_config(live, command, record["prompt"])))
    return flags, {"XDG_CONFIG_HOME": str(root / "config")}, record


# The live tool's keys the hybrid tool keeps: what it serves, how its login is checked, and its Builder's stronger
# attempt ([builder_retry]), so a hybrid run retries and escalates its Builder as a natural run on that tool does.
COPIED_KEYS = ("models", "models_command", "version_command", "auth", "builder_retry")


def tool_config(live: dict, command: list[str], prompt: str) -> dict:
    """The hybrid tool's config: this run's stage command in front of the live tool's models, roles and policy."""
    return {
        "name": NAME,
        "command": command,
        "prompt": prompt,
        **{key: live[key] for key in COPIED_KEYS if key in live},
        "roles": live["roles"],
    }


def calls(out: Path) -> list[dict]:
    """Every call the hybrid tool served, in order: stage, repair, side, role, model, report."""
    path = Path(out) / "hybrid" / "trace.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def scripted_outputs(served: list[dict]) -> list[str]:
    """The report paths the scripted side wrote; a stage row whose report is one of them was no model's."""
    return [row["report"] for row in served if row.get("side") == "scripted" and row.get("report")]


def block(record: dict, served: list[dict], state: dict) -> dict:
    """result.json's ``hybrid``: the route, every call and its side, and the model stages each side served."""
    scripted = scripted_outputs(served)
    rows = [
        row
        for row in state.get("stages") or []
        if isinstance(row, dict) and not row.get("runner_owned") and row.get("stage") != "orchestrator"
    ]
    return {
        "scripted": record["scripted"],
        "first_attempt": record["first_attempt"],
        "live_tool": record["live_tool"],
        "calls": [{key: row.get(key) for key in ("stage", "repair", "side", "model")} for row in served],
        "scripted_stage_names": [row.get("stage") for row in rows if resolver_calls.is_scripted(row, scripted)],
        "live_stage_names": [row.get("stage") for row in rows if not resolver_calls.is_scripted(row, scripted)],
    }


def toml(table: dict) -> str:
    """A provider config as TOML: top-level scalars and arrays, then one [table] per dict (inline tables inside)."""
    lines, tables = [], []
    for key, value in table.items():
        (tables if isinstance(value, dict) else lines).append((key, value))
    text = [f"{key} = {_value(value)}" for key, value in lines]
    for key, value in tables:
        text += ["", f"[{key}]", *(f"{name} = {_value(item)}" for name, item in value.items())]
    return "\n".join(text) + "\n"


def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(key)} = {_value(item)}" for key, item in value.items()) + " }"
    raise TypeError(f"no TOML for {value!r}")
